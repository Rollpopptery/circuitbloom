import re

class PromptWatchdog:
    MIN_NOTE_LEN = 20

    def __init__(self, prompt_file: str):
        self.prompt_file = prompt_file
        self.prompts, self.config = self._parse(prompt_file)
        self.counter = 1
        self.retry_counts = {}
        self.last_note = None
        self.note_log = []  # list of (counter, note) for the whole session

    def _reload(self):
        """Re-parse the prompt file so edits take effect without a restart.
        Preserves counter and retry_counts."""
        self.prompts, self.config = self._parse(self.prompt_file)

    def _parse(self, path: str) -> tuple:
        with open(path) as f:
            text = f.read()

        # Parse config block
        config = {}
        config_match = re.search(r'#CONFIG#(.*?)#PROMPT', text, re.DOTALL)
        if config_match:
            for line in config_match.group(1).strip().splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    config[k.strip()] = v.strip()

        # Parse prompt blocks
        blocks = re.split(r'(#PROMPT(\d+)#)', text)
        prompts = {}
        i = 0
        while i < len(blocks):
            if re.match(r'#PROMPT\d+#', blocks[i]):
                num = int(blocks[i+1])
                body = blocks[i+2].strip()
                prompts[num] = self._parse_block(body)
                i += 3
            else:
                i += 1

        return prompts, config

    def _parse_block(self, body: str) -> dict:
        goto_match    = re.search(r'#GOTO#\s*PROMPT(\d+)', body)
        on_pass_match = re.search(r'#ON PASS#\s*->\s*PROMPT(\d+)', body)
        on_fail_match = re.search(r'#ON FAIL#\s*->\s*PROMPT(\d+)', body)
        on_yes_match  = re.search(r'#ON YES#\s*->\s*PROMPT(\d+)', body)
        on_no_match   = re.search(r'#ON NO#\s*->\s*PROMPT(\d+)', body)

        clean = re.sub(r'#(ON PASS|ON FAIL|ON YES|ON NO|GOTO)#.*', '', body).strip()

        return {
            "text":     clean,
            "goto":     int(goto_match.group(1))    if goto_match    else None,
            "on_pass":  int(on_pass_match.group(1)) if on_pass_match else None,
            "on_fail":  int(on_fail_match.group(1)) if on_fail_match else None,
            "on_yes":   int(on_yes_match.group(1))  if on_yes_match  else None,
            "on_no":    int(on_no_match.group(1))   if on_no_match   else None,
        }

    def _check_retry_limit(self, target: int) -> bool:
        retry_prompt = int(self.config.get("retry_prompt", "1").replace("PROMPT", ""))
        max_retries  = int(self.config.get("max_retries", 999))

        if target == retry_prompt:
            count = self.retry_counts.get(target, 0) + 1
            self.retry_counts[target] = count
            if count > max_retries:
                return True
        return False

    def _validate_note(self, note) -> str | None:
        """Return an error string if the note is unacceptable, else None."""
        if note is None or not isinstance(note, str):
            return (
                "A `note` is required on every hit_watchdog call. "
                "Describe what you did or considered for the current step."
            )
        stripped = note.strip()
        if len(stripped) < self.MIN_NOTE_LEN:
            return (
                f"Note too short ({len(stripped)} chars, minimum {self.MIN_NOTE_LEN}). "
                "Write a real sentence about what you actually did for this step."
            )
        if self.last_note is not None and stripped == self.last_note.strip():
            return (
                "Note is identical to the previous note. Each step requires a "
                "step-specific answer — duplicates suggest batching."
            )
        return None

    def hit(self, signal: str = None, note: str = None) -> dict:
        """
        Agent calls this to get the next prompt.
        signal: None (just advance), or 'PASS'/'FAIL'/'YES'/'NO' for branching steps.
        note: required text describing what was done for the current step.

        Returns dict:
          - prompt: str | None
          - status: 'ok' | 'done' | 'abort' | 'rejected'
          - counter: current prompt number
          - retries: retry count for the retry_prompt
        """
        self._reload()

        err = self._validate_note(note)
        if err is not None:
            return {
                "prompt": None,
                "status": "rejected",
                "counter": self.counter,
                "retries": self.retry_counts.get(self.counter, 0),
                "reason": err,
            }

        self.last_note = note.strip()
        self.note_log.append((self.counter, note.strip()))

        step = self.prompts.get(self.counter)
        if not step:
            return {"prompt": None, "status": "done", "counter": self.counter, "retries": 0}

        sig = signal.upper() if signal else None

        if   sig == "PASS" and step["on_pass"]: next_counter = step["on_pass"]
        elif sig == "FAIL" and step["on_fail"]: next_counter = step["on_fail"]
        elif sig == "YES"  and step["on_yes"]:  next_counter = step["on_yes"]
        elif sig == "NO"   and step["on_no"]:   next_counter = step["on_no"]
        elif step["goto"]:                       next_counter = step["goto"]
        else:                                    next_counter = self.counter + 1

        if self._check_retry_limit(next_counter):
            max_retries = self.config.get("max_retries", "?")
            return {
                "prompt": (
                    f"WATCHDOG ABORT: PROMPT{next_counter} has been retried "
                    f"{max_retries} times. The current trace cannot be routed. "
                    f"Mark it as failed, report the reason, and stop."
                ),
                "status":  "abort",
                "counter": self.counter,
                "retries": self.retry_counts.get(next_counter, 0)
            }

        self.counter = next_counter
        retry_prompt_num = int(self.config.get("retry_prompt", "1").replace("PROMPT", ""))

        next_step = self.prompts.get(self.counter)
        if not next_step:
            return {"prompt": None, "status": "done", "counter": self.counter, "retries": 0}

        return {
            "prompt":  next_step["text"],
            "status":  "ok",
            "counter": self.counter,
            "retries": self.retry_counts.get(retry_prompt_num, 0)
        }

    def is_done(self) -> bool:
        return self.counter not in self.prompts