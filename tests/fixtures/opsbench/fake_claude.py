"""A stand-in for the Claude Code CLI in the operations-benchmark tests.

It never calls a model. It reads the prompt from stdin, records what the session could
see (argv, working directory, environment) in ``fake_probe.json`` in its working
directory, and prints a stream-json transcript chosen by the plan in ``$OPSFAKE_PLAN``:
a JSON object mapping ``"<model>|<task>|<arm>"`` to a list of behaviours, one per attempt
(the attempt count is kept in ``$OPSFAKE_STATE``). Behaviours:

- ``{"reply": "<final text>", "command": "<Bash input>"}``: a normal session with one Bash
  call (``command`` defaults to ``python analyse.py``);
- ``"auth"``: an authentication failure;
- ``"silent"``: no output at all, exit code 1;
- ``"killed"``: one assistant turn, then the process ends without a result;
- ``"api_error"``: an API error result after a turn.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    model = argv[argv.index("--model") + 1]
    work = Path.cwd()
    task_md = (work / "task.md").read_text(encoding="utf-8")
    task = task_md.split("TASK-ID:", 1)[1].split()[0]
    arm = "code-hint" if "Method checklist" in prompt else "code+"
    key = f"{model}|{task}|{arm}"
    state = Path(os.environ["OPSFAKE_STATE"]) / key.replace("|", "__").replace("+", "plus")
    attempt = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(attempt))
    plan = json.loads(Path(os.environ["OPSFAKE_PLAN"]).read_text(encoding="utf-8"))
    behaviours = plan.get(key, [{"reply": "no answer"}])
    behaviour = behaviours[min(attempt, len(behaviours)) - 1]
    probe = {
        "argv": argv,
        "cwd_files": sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()),
        "env": {k: v for k, v in os.environ.items() if k.upper().startswith(("WPBENCH", "MPL",
                                                                            "VIRTUAL_ENV",
                                                                            "PIP_", "UV_"))},
        "path_head": os.environ.get("PATH", "").split(os.pathsep)[0],
        "prompt": prompt,
        "attempt": attempt,
    }  # fmt: skip
    (work / "fake_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    out = []
    init = {"type": "system", "subtype": "init", "model": f"claude-fake-{model}",
            "tools": ["Bash", "Read", "Write", "Edit"], "mcp_servers": []}  # fmt: skip
    if behaviour == "silent":
        return 1
    out.append(init)
    if behaviour == "auth":
        out += [
            {"type": "system", "subtype": "api_retry", "attempt": 1, "error_status": 401,
             "error": "authentication_failed"},
            {"type": "result", "subtype": "success", "is_error": True,
             "result": "Not logged in · Please run /login", "num_turns": 1,
             "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0}},
        ]  # fmt: skip
    else:
        command = "python analyse.py"
        if isinstance(behaviour, dict):
            command = behaviour.get("command", command)
        out.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": command}}]}})  # fmt: skip
        if behaviour == "killed":
            print("\n".join(json.dumps(m) for m in out), flush=True)
            return 137
        result = {"type": "tool_result", "tool_use_id": "t1", "content": "head deficit 8.9"}
        out.append({"type": "user", "message": {"content": [result]}})
        if behaviour == "api_error":
            out.append({"type": "result", "subtype": "success", "is_error": True,
                        "api_error_status": 529, "result": "API Error: 529 overloaded",
                        "num_turns": 2, "total_cost_usd": 0.01,
                        "usage": {"input_tokens": 10, "output_tokens": 5}})  # fmt: skip
        else:
            text = behaviour["reply"]
            out.append({"type": "assistant", "message": {"content": [
                {"type": "text", "text": text}]}})  # fmt: skip
            out.append({"type": "result", "subtype": "success", "is_error": False,
                        "result": text, "num_turns": 3, "duration_ms": 4200,
                        "total_cost_usd": 0.25,
                        "usage": {"input_tokens": 100, "output_tokens": 50}})  # fmt: skip
    print("\n".join(json.dumps(m) for m in out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
