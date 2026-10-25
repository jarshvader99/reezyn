"""Deploy the site directory through Hostinger's hosting MCP server.

The Hostinger extension exposes its MCP servers over stdio; this speaks the
protocol directly so a release can deploy without an editor open.

    python packaging/deploy_site.py --domain reezyn.elastikmind.com --path site
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

SERVER = ["npx", "-y", "--package=@hostinger/mcp@latest", "hostinger-hosting-mcp"]


class McpError(RuntimeError):
    pass


class HostingerMcp:
    def __init__(self, timeout: float = 900.0) -> None:
        self.timeout = timeout
        self._id = 0
        self._lines: queue.Queue[str] = queue.Queue()
        self._stderr: list[str] = []
        self.process = subprocess.Popen(
            SERVER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._drain, args=(self.process.stdout, self._lines), daemon=True).start()
        threading.Thread(target=self._collect_stderr, daemon=True).start()
        self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "reezyn-release", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _drain(self, stream, sink: queue.Queue[str]) -> None:
        for line in stream:
            sink.put(line)

    def _collect_stderr(self) -> None:
        for line in self.process.stderr:
            self._stderr.append(line.rstrip())

    def _send(self, message: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _call(self, method: str, params: dict) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        deadline = time.time() + self.timeout
        while (remaining := deadline - time.time()) > 0:
            try:
                line = self._lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if not line.startswith("{"):
                continue
            message = json.loads(line)
            if message.get("id") == self._id:
                return message
        raise McpError(f"no reply to {method}: {self.tail}")

    @property
    def tail(self) -> str:
        return " | ".join(self._stderr[-5:]) or "server produced no diagnostics"

    def execute(self, operation: str, params: dict) -> str:
        reply = self._call("tools/call", {"name": "execute", "arguments": {"operation": operation, "params": params}})
        if "error" in reply:
            raise McpError(json.dumps(reply["error"]))
        content = reply.get("result", {}).get("content", [])
        return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the site through Hostinger MCP.")
    parser.add_argument("--domain", default="reezyn.elastikmind.com")
    parser.add_argument("--path", default="site")
    parser.add_argument("--type", default="static", choices=["static", "nodejs"])
    args = parser.parse_args()

    directory = Path(args.path).resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"no index.html in {directory}")

    client = HostingerMcp()
    try:
        result = client.execute(
            "hosting_deploy",
            {"domain": args.domain, "path": str(directory), "type": args.type},
        )
    except McpError as error:
        print(f"deploy failed: {error}", file=sys.stderr)
        print(
            "The Hostinger MCP server authenticates through the Hostinger extension; "
            "open the extension once in your editor if this keeps failing.",
            file=sys.stderr,
        )
        return 1
    finally:
        client.close()

    print(result)
    return 0 if '"status":"deployed"' in result or '"status": "deployed"' in result else 1


if __name__ == "__main__":
    sys.exit(main())
