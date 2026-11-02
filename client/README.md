# gecko-suite-client

The small Python package Notes, GAM and GVC use to call Gecko Suite: chat with tool
calling, a shared agent loop, embeddings and web search. It keeps each app's change to a
few lines and gives every app the same error types.

```bash
pip install "gecko-suite-client @ git+https://github.com/davior/gecko-suite@main#subdirectory=client"
```

```python
from gecko_suite_client import SuiteClient, SuiteChat, run_agent

# As the signed-in user, forwarding their token (what a request handler does)...
client = SuiteClient("https://suite.geckopico.com", token=user_jwt)
# ...or as the app, on a user's behalf (what a background job does).
client = SuiteClient("https://suite.geckopico.com", app="gam", app_secret=SECRET, on_behalf_of=user_id)

chat = SuiteChat(client, task="chat.assistant", app="gam")
conversation = chat.conversation(system=[REFERENCE], tools=TOOLS, history=[], prompt="Find sunsets")
outcome = run_agent(conversation, handlers={"search_assets": search_assets}, max_rounds=12)
```

`SuiteNotConfigured` means no model is routed for the task: treat it the way the app
treats "no AI provider" today. `SuiteUnavailable` means the suite could not be reached:
degrade, don't crash.
