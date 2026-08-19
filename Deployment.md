# Step-by-Step Deployment Guide: SecOps Weekly Operations & Reporting Agent

This guide provides an end-to-end walkthrough for deploying the **SecOps Weekly Operations & Reporting Agent** into **Google Cloud Vertex AI Agent Engine (Reasoning Engine)** and registering it within **Gemini Enterprise**.

All configuration across the codebase is **100% variable-driven** with zero hardcoded credentials or project identifiers.

---

## 1. Architecture Overview

The agent executes inside **Vertex AI Agent Engine (Reasoning Engine)** using **Gemini 2.5 Flash** to communicate directly with IAM-protected Model Context Protocol (MCP) servers on Google Cloud Run:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                                 Gemini Enterprise                                 │
│                 (Custom Agent: SecOps Reporting Agent)                            │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │ Intent Routing & Invocation
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                              Google Cloud Platform                                │
│                                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────┐  │
│  │ Vertex AI Agent Engine (Reasoning Engine)                                   │  │
│  │ Resource: projects/<PROJECT_ID>/locations/<REGION>/reasoningEngines/<ID>   │  │
│  └──────────────────┬───────────────────────────────────┬──────────────────────┘  │
│                     │ (Vertex AI Gemini 2.5 Flash)      │ (OIDC ID Token via ADC) │
│                     ▼                                   ▼                         │
│             ┌───────────────┐           ┌──────────────────────────────────────┐  │
│             │ Vertex AI     │           │ Cloud Run MCP Servers (IAM Protected)│  │
│             │ Gemini Models │           │ ├── SecOps MCP (${SECOPS_MCP_URL})   │  │
│             └───────────────┘           │ └── GTI MCP    (${GTI_MCP_URL})      │  │
│                                         └──────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Remote MCP Server Endpoints

The agent connects to remote Model Context Protocol (MCP) servers deployed on Cloud Run:

| MCP Server | Cloud Run Service | Transport | Endpoint URL Variable |
|---|---|---|---|
| **Google Threat Intelligence (GTI)** | `${GTI_SERVICE_NAME}` (e.g. `mcp-gti-mcp-server`) | Streamable HTTP | `${GTI_MCP_URL}` (e.g. `https://<GTI_SERVICE_URL>/mcp`) |
| **Google SecOps (Chronicle)** | `${SECOPS_SERVICE_NAME}` (e.g. `mcp-secops-mcp-server`) | Streamable HTTP | `${SECOPS_MCP_URL}` (e.g. `https://<SECOPS_SERVICE_URL>/mcp`) |

---

## 3. Prerequisites & Environment Setup

### Required Tools
- **Google Cloud SDK** (`gcloud` CLI)
- **Python 3.10, 3.11, or 3.12** with an active virtual environment
- **Google Agent Development Kit** (`google-adk[mcp]>=0.1.0` or `google-adk>=2.7.0`)

---

## 4. Step-by-Step Deployment to Vertex AI Reasoning Engine

### Step 4.1: Configure `.env` & Authenticate

Create or update your `.env` file in the project directory with your project and service settings:

```env
# Google Cloud & Vertex AI Platform
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<YOUR_PROJECT_ID>
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_API_USE_CLIENT_CERTIFICATE=false
SECOPS_AGENT_MODEL=gemini-2.5-flash

# Deployment & Service Account Settings
DISPLAY_NAME=reporting_agent
SA_NAME=reporting-agent-sa
MCP_PROJECT_ID=<YOUR_MCP_PROJECT_ID>

# Remote Cloud Run MCP Server Settings
GTI_SERVICE_NAME=<YOUR_GTI_SERVICE_NAME>
SECOPS_SERVICE_NAME=<YOUR_SECOPS_SERVICE_NAME>
GTI_MCP_URL=https://<YOUR_GTI_SERVICE_URL>/mcp
SECOPS_MCP_URL=https://<YOUR_SECOPS_SERVICE_URL>/mcp

# Observability & Telemetry
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
```

Load your configuration into your terminal and authenticate:

```bash
# 1. Export variables from .env into your shell session
export $(grep -v '^#' .env | xargs)
export SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
export MCP_PROJECT_ID="${MCP_PROJECT_ID:-$GOOGLE_CLOUD_PROJECT}"

# 2. Authenticate with Google Cloud
gcloud auth login
gcloud auth application-default login
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
```

---

### Step 4.2: Enable Required Google Cloud APIs

```bash
gcloud services enable \
    aiplatform.googleapis.com \
    discoveryengine.googleapis.com \
    run.googleapis.com \
    iam.googleapis.com \
    cloudresourcemanager.googleapis.com \
    --project="$GOOGLE_CLOUD_PROJECT"
```

---

### Step 4.3: Configure Service Account & IAM Permissions

Create the dedicated Service Account and grant the necessary roles for Vertex AI Gemini model invocation and Cloud Run MCP server access:

```bash
# 1. Create Dedicated Service Account
gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Reporting Agent Service Account" \
    --project="$GOOGLE_CLOUD_PROJECT"

# 2. Grant Vertex AI User role (for Gemini model invocation)
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user"

# 3. Grant Cloud Run Invoker on GTI MCP Server
gcloud run services add-iam-policy-binding "$GTI_SERVICE_NAME" \
    --project="$MCP_PROJECT_ID" \
    --region="$GOOGLE_CLOUD_LOCATION" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker"

# 4. Grant Cloud Run Invoker on SecOps MCP Server
gcloud run services add-iam-policy-binding "$SECOPS_SERVICE_NAME" \
    --project="$MCP_PROJECT_ID" \
    --region="$GOOGLE_CLOUD_LOCATION" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker"

# 5. Grant Service Account User to deploying user (for deployment impersonation)
export USER_EMAIL=$(gcloud config get-value account)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --member="user:${USER_EMAIL}" \
    --role="roles/iam.serviceAccountUser" \
    --project="$GOOGLE_CLOUD_PROJECT"
```

---

### Step 4.4: Deploy to Vertex AI Agent Engine (Reasoning Engine)

Deploy the agent directly from the project directory:

```bash
adk deploy agent_engine \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="${GOOGLE_CLOUD_LOCATION}" \
  --display_name="${DISPLAY_NAME}" \
  .
```

> **Optional:** To explicitly attach the dedicated service account created in Step 4.3, add `--service_account="${SA_EMAIL}"` to the deployment command.
>
> **Note:** ADK automatically discovers `.agent_engine_config.json` and `requirements.txt` directly inside the current directory (`.`).

Upon successful deployment, ADK outputs the Reasoning Engine resource identifier:
```text
Agent Engine deployed successfully.
Resource Name: projects/<PROJECT_NUMBER>/locations/<REGION>/reasoningEngines/<ENGINE_ID>
```

---

### Step 4.5: Register & Publish in Gemini Enterprise

Registering the Reasoning Engine in **Gemini Enterprise** enables users across your organization to interact with the Reporting Agent directly.

#### 1. Grant Vertex AI Access to Gemini Enterprise Service Agent
```bash
# Retrieve GCP Project Number
export PROJECT_NUMBER=$(gcloud projects describe "${GOOGLE_CLOUD_PROJECT}" --format="value(projectNumber)")

# Grant Vertex AI User role to Discovery Engine service agent
gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
```

#### 2. Register Agent in Google Cloud Console
1. Open the **[Google Cloud Console](https://console.cloud.google.com/)**.
2. Navigate to **Vertex AI** > **Agent Builder** > **Gemini Enterprise** (or **Discovery Engine > Apps**).
3. Select your Enterprise Chat Application.
4. In the left navigation, go to **Agents / Reasoning Engines**:
   - Click **+ Add Agent** / **Register Reasoning Engine**.
   - Select **Custom agent via Agent Runtime** (or **Agent Engine**).
5. Fill in the agent details:
   - **Display Name:** `SecOps Weekly Operations Report Agent`
   - **Description:** `Autonomous agent that synthesizes operational metrics, case resolution breakdown, playbook automation updates, and remediation actions from Google SecOps and GTI into comprehensive Weekly Operations Reports.`
   - **Routing Prompt / Tool Description:**
     ```text
     Use this agent when the user requests a SecOps Weekly Operations Report, case resolution and MTTR breakdown, playbook automation updates, key remediations audit (host isolations, IP blocks, account resets), or SOC operational insights.
     ```
   - **Reasoning Engine Resource:**
     ```text
     projects/<PROJECT_NUMBER>/locations/<REGION>/reasoningEngines/<ENGINE_ID>
     ```
   - **Authentication:** **Google Cloud IAM / Service Account** (default).
6. Click **Save & Publish**.
