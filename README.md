# AI Smart Food Ordering & Restaurant Assistant

A layered food-ordering capstone built from the supplied case study. Gemini replaces the handbook's OpenAI integration. The app combines a conventional REST/UI workflow with one Gemini-powered assistant, document-grounded RAG, and confirmation-gated business actions.

## Architecture

```text
Browser (semantic HTML, CSS, Fetch)
        |
FastAPI routers -> Pydantic validation -> authentication / role checks
        |
Services (catalog, orders, chat) -> repositories -> SQLAlchemy -> MySQL
        |
Single agent (allowlisted Gemini function calling)
   | live read tools / confirmation proposals
   | policy retrieval -> Gemini embeddings -> ChromaDB
   + general response -> Gemini
```

- **API/controllers:** `app/api/` maps HTTP contracts to services; routers do not contain business rules.
- **Services:** `app/services/` owns catalog/order rules, transactions, ownership checks, and chat persistence.
- **Repositories:** `app/repositories/` isolates database reads and query construction.
- **Persistence:** `app/models/` defines MySQL entities for users, menu, orders, chat, tool audit, pending actions, and documents.
- **AI provider:** `app/ai/gemini.py` wraps Gemini generation and embeddings.
- **Agent:** `app/agents/` defines one bounded agent and its allowlisted tools. It reads live business data through existing services. Write tools only create a pending action; only the user's explicit confirmation reaches the order service.
- **RAG:** `app/rag/` extracts PDF/DOCX/TXT, chunks with overlap, indexes Gemini embeddings in ChromaDB, and returns document/page/chunk citations.
- **Frontend:** `frontend/` is a responsive, same-origin HTML/CSS/JavaScript client. Chat text is rendered with `textContent`; Basic credentials are held in memory, not browser storage.
- **Gemini vs. agent:** Gemini is the generative/embedding model provider; the single agent is the application orchestrator that chooses from server-owned tools, validates results, and coordinates confirmation. The model never owns authorization, database writes, or credentials.

### GenAI work vs. agent work

- **GenAI (Gemini 4 Hydrogen):** understands the user's language, selects declared function calls, composes responses from tool results, and embeds document/query text for retrieval. Gemini is not trusted with price, stock, ownership, authorization, or transaction outcomes.
- **Single agent:** applies a bounded tool loop, calls the live catalog/order/policy service methods, records each tool run, plans exact create/cancel actions, and returns confirmation prompts. It does not duplicate business rules or directly write orders.
- **Backend confirmation path:** a user button confirms a persisted pending action; services recheck expiry, owner, status, stock, and price, then commit with an idempotency key.

The agent tools call the same application services in-process rather than making an HTTP request back into their own server. This keeps the authenticated user context server-side while reusing the exact business rules and ownership checks used by REST routes.

### Gemini tool calling

The agent declares a fixed set of functions to Gemini. When Gemini requests one, the server validates and executes it, saves a tool audit record, and returns the result to Gemini for its answer. The conversation is limited to three tool rounds and six tool calls.

| Tool | Purpose |
|---|---|
| `search_menu_items` | Search the active menu with filters. |
| `get_menu_item` | Read a specific active menu item. |
| `check_availability` | Check available portions without reserving them. |
| `get_order_status` | Read an order using the authenticated user's access rights. |
| `retrieve_policy` | Retrieve indexed document evidence and citations. |
| `propose_order` | Prepare an order for explicit user confirmation; it does not place the order. |
| `propose_cancel` | Prepare a cancellation for explicit user confirmation; it does not cancel the order. |

For example, ask the assistant: “Find a vegetarian dish, check if two portions are available, and prepare an order.” Review the displayed details and use the confirm or reject button. A successful tool call is not itself a confirmed order. The Gemini API key must be configured for chat.

## SOLID and design choices

- **Single Responsibility:** routers handle transport, services handle domain rules, repositories handle persistence, Gemini wraps provider APIs, and the agent orchestrates tools.
- **Open/Closed:** add a tool through a declaration and allowlisted handler without moving business rules into the model.
- **Liskov Substitution:** service dependencies are passed at the boundary, allowing test database/session substitutes.
- **Interface Segregation:** feature routers and service methods expose focused contracts rather than one general-purpose controller.
- **Dependency Inversion:** routes depend on service interfaces/construction boundaries, and services receive SQLAlchemy sessions rather than opening connections themselves.
- **Repository, Service Layer, and Unit of Work:** repositories isolate reads, services enforce rules, and SQLAlchemy transactions atomically update inventory and orders.
- **Strategy/Adapter:** `GeminiClient` isolates the model provider; the retriever isolates vector search.
- **Human-in-the-loop command flow:** pending actions capture exact order/cancel terms; confirmation rechecks price, stock, owner, and status before applying a stable idempotency key.

## Run locally

Requirements: Python 3.11+, a running MySQL server, and a Gemini API key for assistant generation/document indexing.

Create a MySQL database named `food_ai` and a user with access to it, or use an existing database and user. Update `DATABASE_URL` in `.env` to match your MySQL host, port, username, password, and database name.

```bash
cp .env.example .env
# Set DATABASE_URL and GEMINI_API_KEY in .env. Keep it private.
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. API docs: <http://127.0.0.1:8000/docs>.

Demo credentials are `customer / customer123!` and `admin / admin123!`. Change these before exposing the app. The startup seed creates two users and ten fictional menu items. `DATABASE_URL` in `.env.example` is an example MySQL connection; replace its credentials with your own. A local SQLite URL can be used for a quick isolated run, but MySQL is the target database.

### Gemini and RAG

The assistant requires `GEMINI_API_KEY`. Gemini handles natural-language responses, tool selection, and embeddings; ChromaDB stores retrieved document vectors locally under `CHROMA_PATH`. Sign in as `admin`, upload a PDF, DOCX, or UTF-8 TXT policy file in the Admin tab, then ask a policy/ingredient question. Answers cite the indexed file and page/section. The sample policy files are under `knowledge_base/`.

If Gemini is unavailable, the client retries transient errors up to three attempts; if it still fails, the API returns an error and persists the failed assistant turn rather than pretending the request succeeded. Rate limits return `429`; service failures return `503`. The menu and manual order REST workflows work independently of Gemini.

### Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL; default sample uses MySQL |
| `GEMINI_API_KEY` | Server-side Gemini API key |
| `GEMINI_MODEL` | Generative model |
| `GEMINI_EMBEDDING_MODEL` | Embedding model |
| `CHROMA_PATH` | Persistent local vector database directory |
| `ORDER_ACTION_TTL_MINUTES` | Confirmation expiration |
| `MAX_UPLOAD_BYTES` | Document upload size limit |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Seeded administrator |
| `USER_USERNAME`, `USER_PASSWORD` | Seeded customer |

Use HTTPS outside localhost. Do not commit `.env`, passwords, or API keys. For production, add migrations, rate limiting, secret rotation, audit/retention policy, and a dedicated MySQL account with least privilege.

## Tests

```bash
.venv/bin/pytest -q
```

Tests use an isolated in-memory SQLite database and do not call Gemini. They cover role/owner checks, validation, stock restoration, order idempotency, confirmation replay, persisted assistant failures, agent tool allowlisting, and document chunking.

## Scope notes

- Payments and delivery are simulated; no provider credentials or real transactions are used.
- The browser's manual order path works without the model; the chat agent uses live tools for data and confirmation for writes.
- The initial Alembic migration is under `migrations/versions/`; apply schema changes with `alembic upgrade head`. Startup also uses SQLAlchemy `create_all` as a development bootstrap. Review migration plans before production upgrades.
