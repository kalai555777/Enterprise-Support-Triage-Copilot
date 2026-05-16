Markdown
# Technical System Design: Enterprise Support & Triage Copilot (ESTC)

## 1. High-Level System Architecture
The ESTC architecture uses a decoupled, distributed microservices approach. It isolates high-speed ML categorization from stateful agent logic, leveraging the Model Context Protocol (MCP) as a secure backend translation standard.

              [ Web UI Client (Streamlit / FastHTML) ]
                                 │
                                (REST / Event Stream)
                                 ▼
                  [ LangGraph Orchestration Engine ]
                    │            │              │
   ┌────────────────┘            │              └────────────────┐
   ▼                             ▼                               ▼
[ PyTorch Classifier ]     [ LangChain RAG Engine ]       [ Model Context Protocol ]
(Local DistilBERT API)     (ChromaDB Vector Store)        ├── PostgreSQL MCP Server
└── GitHub MCP Server


## 2. Component Technical Deep-Dive

### Component A: The Intent Routing Layer (PyTorch)
* **Technology:** PyTorch, Hugging Face Transformers.
* **Model Selection:** `distilbert-base-uncased` fine-tuned for sequence classification or a quantized `Llama-3-8B-Instruct` run locally using 4-bit QLoRA configurations if context length demands semantic complexity.
* **Implementation Strategy:** Train the network on a labeled dataset of internal support inquiries. Expose the model through an optimized FastAPI endpoint. This setup provides microsecond intent classifications, completely bypassing external LLM dependency for foundational routing tasks.

### Component B: Secure Context Layer (Model Context Protocol - MCP)
* **Technology:** Anthropic MCP SDK.
* **Architecture:** Host two independent, read-only local MCP servers:
  1. **PostgreSQL Server:** Handles verification of company health records, subscription states, and payment profiles.
  2. **GitHub Server:** Grants access to repository issue states, bug trackers, and recent deployment commit logs.
* **Security Control:** The orchestration model cannot execute raw SQL queries or touch bash command-lines. It can only call predefined tool schemas exposed securely through the protocol abstraction layer.

### Component C: Knowledge Retrieval (LangChain RAG)
* **Technology:** LangChain, ChromaDB (Vector Store), `bge-large-en-v1.5` embeddings.
* **Advanced Pipeline Optimization:**
  * **Semantic Routing:** Splits non-structural inquiries across specialized vector indices.
  * **Parent-Document Retrieval:** Chunks data into fine-grained structural elements for high-precision retrieval matching, but passes broad contextual parent paragraphs to the LLM to preserve technical text readability.

### Component D: Execution State Machine (LangGraph)
* **Technology:** LangGraph, LangChain ChatModels (powered by `gpt-4o-mini` or `claude-3-5-sonnet`).
* **State Topology:**
  * **Node `classify`:** Executes the local PyTorch API; mutates state variable `intent`.
  * **Conditional Router:** Evaluates `intent`. Routes traffic dynamically to specialized worker nodes.
  * **Node `billing_agent` / `bug_agent`:** Coordinates target MCP tool calls and pulls contextual parameters.
  * **Node `supervisor_review`:** Checks output data arrays. Validates output text quality against baseline compliance restrictions. Maps state transitions smoothly.

## 3. Data Schema & Contracts

### Shared Graph State Schema (Python Pydantic)
```python
from pydantic import BaseModel
from typing import List, Optional, Dict

class AgentState(BaseModel):
    ticket_id: str
    raw_issue_text: str
    company_id: str
    intent: Optional[str] = None
    retrieved_context: List[str] = []
    agent_draft_response: Optional[str] = None
    confidence_score: float = 0.0
    requires_escalation: bool = False
    execution_logs: List[str] = []
PostgreSQL MCP Schema Layout
SQL
CREATE TABLE enterprise_customers (
    company_id VARCHAR(50) PRIMARY KEY,
    company_name VARCHAR(100),
    subscription_tier VARCHAR(20), -- 'Enterprise', 'Growth', 'Free'
    account_status VARCHAR(20),    -- 'Active', 'Delinquent', 'Locked'
    technical_poc_email VARCHAR(100)
);
4. Evaluation, Observability & Deployment Framework
Evaluation Driven Development (EDD) Strategy
Framework: Ragas + LangSmith.

Core Metrics Tracked:

Faithfulness: Measures if the drafted support reply remains grounded strictly within the retrieved documentation (detecting hallucinations).

Answer Relevance: Verifies if the draft actually solves the user's explicit problem statement.

Context Recall: Measures if the system retrieved all the information required by the system specification.

Infrastructure & Deployment Setup
The complete runtime ecosystem will be mapped inside a multi-container network managed via docker-compose.yml:

Service 1 (classifier-api): PyTorch FastAPI microservice endpoint handling model weights.

Service 2 (mcp-postgres-server): Postgre database instance isolating client operational records.

Service 3 (orchestrator-app): LangGraph execution worker runtime linked directly with LangSmith tracking endpoints.

Service 4 (ui-client): Streamlit dashboard environment accessible over local networks.