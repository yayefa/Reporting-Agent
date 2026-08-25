# Step-by-Step Deployment Guide: SecOps Weekly Operations & Reporting Agent

This guide provides an end-to-end walkthrough for deploying the **SecOps Weekly Operations & Reporting Agent** to **Google Cloud Vertex AI Agent Engine (Reasoning Engine)** and registering it with **Gemini Enterprise**.

---

## 1. Quick Setup & Environment Variables

Copy and paste the block below into your terminal (or Cloud Shell) after replacing the placeholders with your actual values:

```bash
# Set your Google Cloud configuration
export PROJECT_ID="gemini-entreprise-494918"       # Your GCP Project ID
export REGION="us-central1"                       # Your Vertex AI Region
export DISPLAY_NAME="reporting_agent"             # Agent display name
export SA_NAME="reporting-agent-sa"               # Dedicated service account name

# Remote MCP Server Settings (Cloud Run)
export MCP_PROJECT_ID="${PROJECT_ID}"             # Project where MCP servers run
export GTI_SERVICE_NAME="mcp-gti-mcp-server"      # GTI Cloud Run service name
export SECOPS_SERVICE_NAME="mcp-secops-mcp-server"# SecOps Cloud Run service name

# Computed Variables (No need to edit these)
export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
```

*(Optional)* You can also save these inside a `.env` file in the root folder:
```bash
cp .env.example .env
```

---

## 2. Authenticate & Configure Project

```bash
gcloud config set project "$PROJECT_ID"
gcloud auth application-default login
```

---

## 3. Enable Required Google Cloud APIs

```bash
gcloud services enable \
    aiplatform.googleapis.com \
    discoveryengine.googleapis.com \
    run.googleapis.com \
    iam.googleapis.com \
    cloudresourcemanager.googleapis.com \
    --project="$PROJECT_ID"
```

---

## 4. Create Service Account & Assign IAM Permissions

Grant the agent permissions to invoke Gemini models and call your Cloud Run MCP servers:

```bash
# 1. Create Dedicated Service Account
gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Reporting Agent Service Account" \
    --project="$PROJECT_ID"

# 2. Grant Vertex AI User role (for Gemini 2.5 Flash model invocation)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user"

# 3. Grant Cloud Run Invoker on GTI MCP Server
gcloud run services add-iam-policy-binding "$GTI_SERVICE_NAME" \
    --project="$MCP_PROJECT_ID" \
    --region="$REGION" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker"

# 4. Grant Cloud Run Invoker on SecOps MCP Server
gcloud run services add-iam-policy-binding "$SECOPS_SERVICE_NAME" \
    --project="$MCP_PROJECT_ID" \
    --region="$REGION" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker"

# 5. Grant Service Account User role to your personal account (for deployment)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --member="user:$(gcloud config get-value account)" \
    --role="roles/iam.serviceAccountUser" \
    --project="$PROJECT_ID"
```

---

## 5. Deploy to Vertex AI Agent Engine

Run the deployment command specifying **`reporting_agent`** as the target:

```bash
adk deploy agent_engine \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --display_name="$DISPLAY_NAME" \
  reporting_agent
```

> **Why `reporting_agent` instead of `.`?**  
> ADK uses the target argument as the container's Python application name. Because repository directories often contain hyphens (e.g. `Reporting-Agent`), deploying `.` causes Python import errors in the container. Specifying `reporting_agent` ensures the container loads cleanly and mounts the streaming routes (`POST /api/stream_reasoning_engine`).

### Updating an Existing Deployment (In-Place)
If you already registered an agent and want to update the existing Reasoning Engine instance:
```bash
adk deploy agent_engine \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --display_name="$DISPLAY_NAME" \
  --agent_engine_id="<YOUR_NUMERIC_REASONING_ENGINE_ID>" \
  reporting_agent
```

---

## 6. Register in Gemini Enterprise

Registering the Reasoning Engine enables enterprise chat users to interact with the Reporting Agent.

### 1. Grant Access to Gemini Enterprise Service Agent
```bash
# Retrieve Project Number
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

# Grant Vertex AI User role to Gemini Enterprise / Discovery Engine service agent
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
```

### 2. Register Agent in Google Cloud Console
1. Open the **[Google Cloud Console](https://console.cloud.google.com/)**.
2. Navigate to **Vertex AI** > **Agent Builder** > **Gemini Enterprise** (or **Discovery Engine > Apps**).
3. Select your Enterprise Chat Application.
4. Go to **Agents / Reasoning Engines** > Click **+ Add Agent**.
5. Fill in the details:
   - **Display Name:** `SecOps Weekly Reporting Agent`
   - **Description:** `Autonomous agent that synthesizes operational metrics, case resolution breakdown, playbook automation updates, and remediation actions from Google SecOps (Chronicle) and Google Threat Intelligence (GTI) into executive reports.`
   - **Routing Prompt:**
     ```text
     Use this agent when the user requests weekly or monthly SecOps metrics, SOC operational reports, case resolution statistics, playbook automation performance, or executive incident summaries.
     ```
   - **Reasoning Engine Resource:** Paste the resource name output by the deploy command:
     ```text
     projects/<PROJECT_NUMBER>/locations/<REGION>/reasoningEngines/<ENGINE_ID>
     ```
   - **Authentication:** **Google Cloud IAM / Service Account** (default).
6. Click **Save & Publish**.
