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

### Direto do GitHub (recomendado)

Com [uv](https://docs.astral.sh/uv/) instalado, não é preciso clonar nada —
o `uvx` baixa, instala e executa o servidor a partir deste repositório:

```bash
uvx --from git+https://github.com/brunosemfio/mcp-google-play-console.git play-console-mcp
```

Registrando no Claude Code:

```bash
claude mcp add google-play-console \
  --env GOOGLE_APPLICATION_CREDENTIALS=/caminho/para/chave.json \
  --env PLAY_CONSOLE_GCS_BUCKET=pubsite_prod_rev_0123... \
  -- uvx --from git+https://github.com/brunosemfio/mcp-google-play-console.git play-console-mcp
```

Ou em um `mcp.json` genérico:

```json
{
  "mcpServers": {
    "google-play-console": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/brunosemfio/mcp-google-play-console.git",
        "play-console-mcp"
      ],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/caminho/para/chave.json",
        "PLAY_CONSOLE_GCS_BUCKET": "pubsite_prod_rev_0123..."
      }
    }
  }
}
```

O `uvx` faz cache do build: para atualizar após novos commits, rode o comando
uma vez com `--refresh`. Para fixar uma versão, aponte para uma tag ou commit:
`git+https://...@<tag-ou-sha>`.

### A partir de um clone local (desenvolvimento)

```bash
git clone https://github.com/brunosemfio/mcp-google-play-console.git
cd mcp-google-play-console
python -m venv .venv && source .venv/bin/activate
pip install -e .
play-console-mcp            # stdio (padrão)
play-console-mcp --transport streamable-http --port 8000
```

Nesse caso, registre apontando para o binário do venv:
`claude mcp add google-play-console ... -- /caminho/do/clone/.venv/bin/play-console-mcp`

## Desenvolvimento

```bash
uv sync --extra dev          # ou: pip install -e '.[dev]'
uv run pytest                # testes unitários (offline, com fakes)
uv run ruff check play_console_mcp tests
uv run mypy play_console_mcp

# Testes de integração (batem na API real; precisam de credenciais):
GOOGLE_APPLICATION_CREDENTIALS=/caminho/chave.json uv run pytest -m integration
```

O CI (GitHub Actions, branch `main`) roda ruff, mypy e a suíte unitária com
cobertura mínima de 65% em Python 3.10 e 3.12.

## Exemplos de perguntas

- "Qual a taxa de crash diária do com.example.app nos últimos 14 dias, por versão?"
- "Quais os 10 issues de ANR com mais usuários afetados este mês? Traga um stack trace de exemplo."
- "Baixe o relatório de instalações por país de julho de 2026 e resuma."

## Notas

- Datas (`start_date`/`end_date`) são **inclusivas** nas duas pontas: o servidor converte para os end times exclusivos da API somando um dia, então consultar um único dia (`start = end`) funciona. Datas inexistentes (ex. `2026-02-30`) e intervalos invertidos falham localmente com mensagem clara.
- A agregação diária da Reporting API é ancorada no fuso `America/Los_Angeles` (padrão da API); a horária, em UTC.
- `PLAY_CONSOLE_GCS_BUCKET` aceita o nome do bucket ou a URI completa copiada do Play Console (`gs://pubsite_prod_rev_.../stats/installs/`).
- Apps com poucos usuários podem retornar zero linhas de vitals (limiar de dados do Play) — use `get_metric_set_freshness` para distinguir "sem dados ainda" de "abaixo do limiar".
- Tudo é somente leitura — o servidor não expõe nenhuma operação de escrita no Play Console.
- Os CSVs do Play são majoritariamente UTF-16 (com BOM); o `download_stats_report` detecta o BOM e decodifica UTF-16 ou UTF-8 automaticamente, e devolve as linhas já parseadas em JSON (com flag `truncated` quando `max_rows` é atingido).
- O tamanho do arquivo é verificado nos metadados **antes** do download e revalidado nos bytes recebidos **depois** dele, inclusive após descompressão de `.gz` (`max_bytes`, até 10 MB).
