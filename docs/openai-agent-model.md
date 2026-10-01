# OpenAI Agent in the main chat

Select **OpenAI Agent** from the existing model dropdown, then use the ordinary
chat input and Send/Enter controls. Progress appears in the status line, final
text in the assistant bubble, and generated files in native Chatbot file bubbles.
There is no separate Agent sidebar or plugin installation step.

This optional model uses `client.beta.agents` from the pinned OpenAI SDK 3.13.0,
with an OpenAI-hosted environment and network disabled. A hosted task can execute
code and incur charges; selecting the model displays this before submission.
Project access to the beta API and the selected model is required. The core
application keeps its existing OpenAI 1.x dependency and ordinary providers.

## Setup

From the repository root, create a **separate** runtime:

```sh
python3.11 -m venv .agents-runtime
.agents-runtime/bin/python -m pip install -r optional/agents/requirements.txt
```

On Windows use `.agents-runtime/Scripts/python.exe`. Alternatively set
`CHUANHU_AGENT_PYTHON` to the absolute path of an isolated interpreter. Do not
install this SDK into the core Gradio environment.

Set `CHUANHU_AGENT_API_KEY` in the server environment, or add one assignment to
an ignored `.env.agents` file at the repository root. Keep that file readable
only by the server user. This code does not create keys or copy the application's
ordinary provider key. Requests use the fixed official HTTPS endpoint, no
redirects, no inherited proxy/endpoint/project/organization overrides, and no
automatic SDK retries. The worker suppresses SDK exception bodies and stderr.

The model metadata entry in `modules/presets.py` selects the explicit hosted
model ID (`gpt-6-astra` initially). Configure a model supported by your project.
The default instructions are separate from an ordinary provider's system prompt.
The existing System prompt editor is available before the first remote task;
changing instructions/model/tool permissions requires a new conversation.
Ordinary sampling, single-turn and token controls do not configure this beta API.

## Conversation and recovery

- Existing local chat remains visible when switching models, but only the new
  submitted text and this Agent's explicit instructions are sent remotely.
- Follow-ups use the same owned remote session. Local retrieval, web search,
  uploaded files, legacy plugin tools, provider credentials, billing requests and
  automatic chat-title requests are not sent to it.
- **Stop** submits the remote cancellation event. `cancel_requested` is pending;
  only the target turn's terminal status confirms cancellation. Closing the
  browser/stream alone does not cancel the hosted task.
- The existing **Regenerate** control performs read-only reconciliation for this
  model. It never resubmits input or rewinds local conversation history. After a
  disconnect, reconcile before sending, resetting, deleting or switching models.
- When the session is known but the new turn ID was missed, recovery excludes the
  pre-submission turn-ID baseline and accepts only a unique new root turn. It
  never treats the previous turn, an ambiguous candidate, an idle session, EOF or
  a subagent's completion as success. A pending cancellation is submitted once
  recovery identifies that target.
- A missing runtime/key/SDK or a confirmed rejected initial request is
  `not_started`; fix configuration and explicitly send again. A create request
  whose result is unknown remains `uncertain`. Recovery can search the latest
  100 sessions for its private run ID; it does not automatically resubmit.
- If no safe match is found, retain the private journal and ask the operator to
  reconcile through the official API. Do not guess another session ID.

Capabilities are held privately in memory, bound to the browser session and
local conversation fingerprint. Imported/exported JSON contains ordinary text
and settings; it cannot grant remote session or tool permissions. Importing even
an identical transcript creates no remote authority. Switching back to an
unchanged conversation in the same browser/server process can resume its binding.
A different browser or a server restart does not automatically restore those
capabilities. The ignored `agent_data/` journal contains IDs, status and
fingerprints, never task/answer text or keys, for operator recovery. It is blocked
from Gradio file serving. Normal chat history still contains the visible text.

New chat creates a fresh remote binding. Local history deletion/reset does not
delete a remote session; remote resource retention and deletion remain an operator
action. Downloads are limited to 20 artifacts, 10 MiB per file and 50 MiB total,
obtained through the official artifact API, then registered in Gradio's cache.
The worker never follows artifact URLs. Temporary artifacts and cache files need
the deployment's normal cleanup policy.

The optional `text_statistics` function tool is disabled by default. An operator
can enable it in this model's server metadata (`allow_text_tool: true`); it accepts
only bounded text, has no file/network access and has a ten-call budget. Browser
history metadata cannot enable it. Hosted environment code execution itself is
an API capability, not a claim that arbitrary legacy plugins are safe.

## Offline validation

```sh
python3.11 -m venv .test-runtime
.test-runtime/bin/python -m pip install -r requirements_tests.txt
GRADIO_ANALYTICS_ENABLED=False .test-runtime/bin/python -m pytest -q tests
.test-runtime/bin/python tests/main_chat_preview.py --port 8893
```

The preview executes the actual main layout and selected actual event chains,
factory, model adapter, chat wrappers, base model and locale/theme/assets. Only
provider/backend/config dependencies are synthetic. It never reads config.json
or credentials and never calls a paid API. Main input `slow` starts a synthetic
long task for cancellation testing. Unrelated history/training/settings actions
are intentionally not wired in this preview.

Tests cover isolated runtime events, real factory/send/retry/history behavior,
request injection, switch concurrency, owner isolation, preflight failures,
ambiguous/unknown turn recovery, pending cancellation and malicious imports.
Browser QA verifies main dropdown/send/progress/answer/file bubbles/follow-up,
remote cancellation through the real Stop event chain, and continued conversation.
The browser download automation stalled; cached file/link construction was
verified, but an actual completed browser download is not claimed. This change's
verification uses no new paid API calls, and beta availability remains dependent
on the deployed SDK/project.
