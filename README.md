# Kubernetes AI Agent

A natural language interface for managing Kubernetes clusters. Ask questions in plain English and the agent executes kubectl commands for you.

## Features

- **Natural language commands** — "restart prometheus", "list pods", "scale order to 2"
- **Fuzzy name matching** — Don't need exact resource names; the agent finds what you mean
- **Prefix-aware resolution** — "order" automatically matches `app-order` (configurable)
- **Resource adjustment** — Set CPU, memory, and JVM heap with natural language
- **Cluster switching** — Manage multiple Kubernetes clusters seamlessly
- **Approval workflow** — Sensitive operations require confirmation
- **Log summarization** — Get AI-powered analysis of pod logs

## Prerequisites

- Python 3.9+
- kubectl configured for your cluster(s)
- gcloud (for GKE clusters)
- Redis (for session storage)
- OpenRouter API key (for LLM)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Ankit4510/K8s-agent.git
cd agents
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Start Redis

```bash
# macOS
brew install redis
brew services start redis

# Linux
sudo apt install redis-server
sudo systemctl start redis

# Docker
docker run -d -p 6379:6379 redis
```

### 4. Configure environment variables

Create a `.env` file or export in your shell:

```bash
export OPENROUTER_API_KEY="your-openrouter-api-key"
```

Get an API key at https://openrouter.ai/

### 5. Configure for your cluster

#### Customizing deployment prefixes

If your deployments use a custom prefix (e.g., `myapp-` instead of `app-`), edit these files:

**`skills/resolver.py`:**
```python
PREFIXES = ["all-myapp-", "myapp-"]
APP_PREFIX_ALIASES = ["component-", "comp-"]
```

**`skills/list_skill.py`:**
```python
PREFIXES = ["all-myapp-", "myapp-"]
```

#### Adding your clusters

Edit `cluster_store.py` to add your cluster definitions:

```python
CLUSTERS = {
    "my-gke-cluster": {
        "zone": "us-central1-a",
        "project": "my-gcp-project",
    },
    "prod-cluster": {
        "zone": "us-east1-b",
        "project": "my-prod-project",
    },
}
```

### 6. Start the agent

```bash
./start.sh
```

Or manually:

```bash
python main.py
```

The web UI will be available at `http://localhost:8000`

## Usage Examples

### Basic operations

```
User: list pods
Bot: Shows all pods in the current namespace

User: restart prometheus
Bot: Restarts the prometheus deployment

User: is grafana up?
Bot: Checks grafana deployment status

User: delete pod nginx-abc123
Bot: Deletes the specified pod (requires approval)
```

### Scaling

```
User: scale order to 2
Bot: Scales the order deployment to 2 replicas

User: scale down prometheus
Bot: Scales prometheus to 0 replicas
```

### Resource adjustment

```
User: set cpu of order to 512m
Bot: Updates CPU request to 512m

User: set memory of snh to 4Gi
Bot: Updates memory request to 4Gi

User: set heap of prometheus to 2048m
Bot: Updates JVM heap (-Xmx) to 2048m

User: set cpu to 256m and memory to 2Gi for snh
Bot: Updates both CPU and memory at once
```

### Logs and diagnosis

```
User: check logs of snh and summarize
Bot: Fetches logs and provides AI analysis

User: diagnose order
Bot: Analyzes order deployment for issues

User: what's broken?
Bot: Scans cluster for unhealthy pods
```

### Cluster management

```
User: switch to prod cluster
Bot: Switches active cluster context

User: list clusters
Bot: Shows all configured clusters
```

## Architecture

- **Skills** — Modular, deterministic playbooks for user intents (restart, scale, logs, diagnose, etc.)
- **Resolver** — Fuzzy matching with prefix/suffix normalization for resource names
- **LLM** — Intent classification and log summarization via OpenRouter
- **Redis** — Session and context storage
- **FastAPI** — Web server with approval workflow

## Configuration

### Prefix customization

Edit `skills/resolver.py` to match your deployment naming:

```python
PREFIXES = ["all-app-", "app-"]  # Your deployment prefixes
APP_PREFIX_ALIASES = ["component-", "comp-"]  # User-friendly aliases
```

This enables:
- "order" → matches `app-order`
- "component-order" → matches `app-order` (strips "component-")
- "comp-order" → matches `app-order` (strips "comp-")

### Cluster configuration

Add clusters in `cluster_store.py`:

```python
CLUSTERS = {
    "cluster-name": {
        "zone": "us-central1-a",
        "project": "my-gcp-project",
    },
}
```

### LLM model

Edit `llm.py` to change the model:

```python
MODEL = "openai/gpt-4o-mini"  # Default
# Or: "anthropic/claude-3-haiku", "google/gemma-2-9b-it:free", etc.
```

## Development

### Running tests

```bash
pytest
```

### Adding a new skill

1. Create `skills/my_skill.py` inheriting from `Skill`
2. Implement `execute(self, args, context)` method
3. Register in `skills/__init__.py`
4. Add intent examples to `agent.py`
5. Add routing rule to `llm.py`

See `skills/restart_skill.py` for a reference implementation.

## Troubleshooting

### "No active cluster set"

Run: `switch to <cluster-name>` to set your active cluster first.

### "LLM temporarily unavailable"

Check your `OPENROUTER_API_KEY` is set and has credits. Visit https://openrouter.ai/credits

### "Deployment not found"

- Check the deployment name matches your prefix configuration
- Try the full name instead of the short name
- Use `list deployments` to see what's available

### Redis connection errors

Ensure Redis is running: `redis-cli ping` should return `PONG`

## Security Notes

- Never commit `OPENROUTER_API_KEY` or other secrets to git
- Use environment variables for sensitive configuration
- The approval workflow requires confirmation for destructive operations
- Logs are stored in `agent.log` — exclude from git (see `.gitignore`)

## License

[Your license here]
