from learning.prompt_watchdog import PromptWatchdog

def deliver_to_agent(prompt):
    print(f"\n[TO AGENT]:\n{prompt}\n")

def get_agent_response():
    return input("Agent signal (PASS/FAIL/YES/NO or blank): ").strip() or None


watchdog = PromptWatchdog("routing.txt")
agent_signal = None

while True:
    result = watchdog.hit(signal=agent_signal)

    if result["status"] == "done":
        print("Sequence complete.")
        break

    if result["status"] == "abort":
        print(f"[WATCHDOG ABORT after {result['retries']} retries]")
        deliver_to_agent(result["prompt"])
        break

    print(f"[Step {result['counter']} | Retries: {result['retries']}]")
    deliver_to_agent(result["prompt"])
    agent_signal = get_agent_response()