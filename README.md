# Google Play Console MCP Server

Servidor MCP (Model Context Protocol) somente-leitura para puxar dados de relatório do Google Play Console, inspirado na arquitetura do [googleads/google-ads-mcp](https://github.com/googleads/google-ads-mcp) (Python + FastMCP + Application Default Credentials).

## Fontes de dados

| Fonte | O que cobre |
|---|---|
| [Play Developer Reporting API](https://developers.google.com/play/developer/reporting) | Vitals (taxa de crash, ANR, slow start, wakeups...), issues e reports de erro com stack trace, anomalias detectadas pelo Play |
| Exports CSV no Cloud Storage (`pubsite_prod_*`) | Instalações, avaliações, store performance, assinaturas, ganhos — relatórios que não existem na Reporting API |

## Ferramentas expostas

- `search_accessible_apps` — lista os apps que as credenciais enxergam (use primeiro, para descobrir os package names)
- `list_metric_sets` — descreve os metric sets de vitals disponíveis e suas métricas
- `get_metric_set_freshness` — até quando há dados disponíveis para um metric set
- `query_metric_set` — série temporal de vitals (crash rate por versão, ANR por país, etc.) com dimensões, filtro e paginação
- `search_error_issues` — clusters de crash/ANR (como a página "Crashes e ANRs" do Console)
- `search_error_reports` — reports individuais de erro, com stack trace
- `list_anomalies` — anomalias detectadas nas métricas do app
- `list_stats_reports` / `download_stats_report` — lista e lê os CSVs exportados (installs, ratings, etc.) como linhas JSON, com suporte a `.csv.gz`

Todas as ferramentas são anotadas como somente-leitura (`readOnlyHint`).

## Configuração

### 1. Habilite a API no Google Cloud

No projeto GCP que vai usar, habilite a **Google Play Developer Reporting API**:

```bash
gcloud services enable playdeveloperreporting.googleapis.com
```

### 2. Credenciais (Application Default Credentials)

**Opção A — Service account (recomendado):**

1. Crie uma service account no GCP e baixe a chave JSON.
2. No Play Console, em **Usuários e permissões → Convidar novo usuário**, convide o e-mail da service account com permissão de visualização dos apps (no mínimo "Ver informações do app").
3. Exporte:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/caminho/para/chave.json
```

**Opção B — Suas próprias credenciais de usuário:**

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/playdeveloperreporting,https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/cloud-platform
```

(Sua conta Google precisa ter acesso ao Play Console.)

### 3. Bucket de relatórios CSV (opcional)

Só necessário para `list_stats_reports`/`download_stats_report`. No Play Console, em **Fazer download de relatórios → Estatísticas → Copiar URI do Cloud Storage**, copie o bucket (formato `pubsite_prod_rev_0123...` ou `pubsite_prod_0123...`) e exporte:

```bash
export PLAY_CONSOLE_GCS_BUCKET=pubsite_prod_rev_01234567890987654321
```

Para service accounts, o acesso ao bucket é concedido automaticamente quando a conta é convidada com a permissão de relatórios financeiros/estatísticas no Play Console.

## Instalação e uso

```bash
cd claude
python -m venv .venv && source .venv/bin/activate
pip install -e .
play-console-mcp            # stdio (padrão)
play-console-mcp --transport streamable-http --port 8000
```

### Registrar no Claude Code

```bash
claude mcp add google-play-console \
  --env GOOGLE_APPLICATION_CREDENTIALS=/caminho/para/chave.json \
  --env PLAY_CONSOLE_GCS_BUCKET=pubsite_prod_rev_0123... \
  -- /caminho/para/claude/.venv/bin/play-console-mcp
```

Ou em um `mcp.json` genérico:

```json
{
  "mcpServers": {
    "google-play-console": {
      "command": "/caminho/para/claude/.venv/bin/play-console-mcp",
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/caminho/para/chave.json",
        "PLAY_CONSOLE_GCS_BUCKET": "pubsite_prod_rev_0123..."
      }
    }
  }
}
```

## Desenvolvimento

```bash
pip install -e '.[dev]'
pytest
```

## Exemplos de perguntas

- "Qual a taxa de crash diária do com.example.app nos últimos 14 dias, por versão?"
- "Quais os 10 issues de ANR com mais usuários afetados este mês? Traga um stack trace de exemplo."
- "Baixe o relatório de instalações por país de julho de 2026 e resuma."

## Notas

- A agregação diária da Reporting API é ancorada no fuso `America/Los_Angeles` (padrão da API); a horária, em UTC.
- Tudo é somente leitura — o servidor não expõe nenhuma operação de escrita no Play Console.
- Os CSVs do Play são majoritariamente UTF-16 (com BOM); o `download_stats_report` detecta o BOM e decodifica UTF-16 ou UTF-8 automaticamente, e devolve as linhas já parseadas em JSON (com flag `truncated` quando `max_rows` é atingido).
- O tamanho do arquivo é verificado nos metadados **antes** do download (`max_bytes`, até 10 MB).
