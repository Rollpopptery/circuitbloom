#!/usr/bin/env python3
"""
Route Server — Web-based PCB route viewer for .bloom files.

Port 8083: Browser — Canvas view of pads, tracks, vias
Port 8084: Agent API — routing commands

Usage:
    python route_server.py [path/to/board.bloom]
"""

import argparse
import http.server
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

import route_state as rs
from route_handlers import BrowserHandler, AgentHandler


def run():
    parser = argparse.ArgumentParser(description="Route Server — PCB route viewer")
    parser.add_argument("bloom", nargs="?", default=None,
                        help="Path to .bloom file (optional, starts empty if omitted)")
    parser.add_argument("--board", default=None,
                        help="Path to .kicad_pcb file for CLI commands (DRC)")
    parser.add_argument("--browser-port", type=int, default=8083)
    parser.add_argument("--agent-port", type=int, default=8084)
    args = parser.parse_args()

    rs.bloom_path = args.bloom
    rs.board_path = args.board
    if rs.bloom_path:
        rs.reload_bloom()
    else:
        print("  No bloom file specified — starting empty.")

    class ThreadedHTTPServer(http.server.HTTPServer):
        """Handle each request in a separate thread (needed for SSE)."""
        from socketserver import ThreadingMixIn
        daemon_threads = True
        def process_request(self, request, client_address):
            t = threading.Thread(target=self.finish_request, args=(request, client_address), daemon=True)
            t.start()

    browser = ThreadedHTTPServer(('0.0.0.0', args.browser_port), BrowserHandler)
    agent = ThreadedHTTPServer(('0.0.0.0', args.agent_port), AgentHandler)

    threading.Thread(target=browser.serve_forever, daemon=True).start()
    threading.Thread(target=agent.serve_forever, daemon=True).start()

    print()
    print("  Route Server")
    print("  ============")
    print(f"  Browser:  http://localhost:{args.browser_port}")
    print(f"  Agent:    http://localhost:{args.agent_port}")
    print(f"  Bloom:    {rs.bloom_path or '(empty)'}")
    print()
    print("  Ready. Press Ctrl+C to stop.")
    print()

    last_mtime = None
    try:
        while True:
            time.sleep(1.5)
            if rs.bloom_path:
                try:
                    mtime = os.path.getmtime(rs.bloom_path)
                    if last_mtime is None:
                        last_mtime = mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        print("  [watch] Bloom file changed, reloading...")
                        rs.reload_bloom()
                except OSError:
                    pass
    except KeyboardInterrupt:
        print("\n  Stopped.")
        browser.shutdown()
        agent.shutdown()


if __name__ == '__main__':
    run()
