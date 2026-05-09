# Clawd-Code Local Runtime Setup

This guide records the local deployment flow for running the Clawd-Code agent
runtime without changing source code. Do not commit real API keys or full local
configuration output.

## Summary

- Use a repository-local `.venv` for Python dependencies.
- Use `uv` to create the virtual environment and install packages.
- Store provider settings in `~/.clawd/config.json`.
- Use an OpenAI-compatible API that supports chat completions and tool calling.

## Setup

Enter the repository:

```bash
cd /home/workspace/github/claude_python/Clawd-Code
```

Install `uv`:

```bash
python3 -m pip install --user uv
```

Create the virtual environment:

```bash
UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv venv --python 3.10
```

Install dependencies:

```bash
UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv pip install -r requirements.txt
UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv pip install 'httpx[socks]'
```

`httpx[socks]` is needed when the runtime environment uses a SOCKS proxy.

## Configuration

Create the config directory:

```bash
mkdir -p ~/.clawd
chmod 700 ~/.clawd
```

Create or edit `~/.clawd/config.json`:

```json
{
  "default_provider": "openai",
  "providers": {
    "openai": {
      "api_key": "YOUR_API_KEY_HERE",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "default_model": "GLM-4.5-air"
    }
  },
  "session": {
    "auto_save": true,
    "max_history": 100
  }
}
```

Protect the config file:

```bash
chmod 600 ~/.clawd/config.json
```

## Run

Activate the environment and start the runtime:

```bash
source .venv/bin/activate
python -m src.cli --stream
```

## Docker

Build the CLI image:

```bash
docker build -t clawd-code:local .
```

Check the installed console entry point:

```bash
docker run --rm clawd-code:local --version
```

Run the interactive CLI from the current workspace:

```bash
docker run --rm -it -v "$PWD:/workspace" clawd-code:local
```

Persist CLI configuration across container runs:

```bash
docker run --rm -it \
  -v "$PWD:/workspace" \
  -v "$HOME/.clawd:/home/clawd/.clawd" \
  clawd-code:local
```

## Verification

Check that the CLI can load the configuration:

```bash
.venv/bin/python -m src.cli config
```

Test normal chat in the REPL:

```text
你好，用一句话介绍你自己
```

Test the agent tool loop:

```text
读取 requirements.txt 并用一句话总结
```

Expected result:

- The REPL banner shows `Provider OPENAI Provider`.
- Normal chat returns model text.
- The tool test shows a `Read` tool call, then returns a summary.

## Notes

- The API service must be compatible with OpenAI `chat.completions`.
- Tool calling requires support for OpenAI `tools`, `tool_calls`, and
  `role: tool`.
- Never commit `~/.clawd/config.json`, real API keys, or terminal output that
  includes credentials.
