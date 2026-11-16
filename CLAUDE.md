# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project locally

Three processes are required:

```bash
# Django dev server
python manage.py runserver

# Celery worker (Windows: --pool=solo required)
celery -A cinnamon worker -l info --pool=solo

# Celery Beat (periodic tasks — only needed when working on schedules)
celery -A cinnamon beat -l info
```

Redis must be running (Docker recommended):
```bash
docker-compose up -d redis
```

Database migrations:
```bash
python manage.py migrate
python manage.py makemigrations <app>   # df or usuarios
```

Management commands:
```bash
# Carrega o plano de contas único (abas do Plano Referencial RFB). Idempotente; valida a árvore antes de gravar.
python manage.py importar_plano_contas plano_contas.xlsx --aba L100A --aba L300A

# Gera o link de cadastro de um usuário para um escritório de contabilidade (não envia e-mail por padrão)
python manage.py convidar_escritorio pessoa@x.com --escritorio "Nome" [--papel ADMIN] [--criar-escritorio] [--enviar]
```

## Architecture overview

### Multi-tenancy model
Every user belongs to one or more **Empresa** (companies) via a **Membership** join table, or reaches companies through an **EscritorioContabil** (accounting firm): `MembroEscritorio` → escritório → `VinculoContabil` → Empresa (one active firm per company). **`usuarios/utils/acesso.py::empresas_acessiveis(user)` is the single source of "which companies can this user open"** — use it instead of querying `Membership` directly. The active company is stored in `request.empresa_ativa` (injected by `EmpresaAtivaMiddleware` from session key `"empresa_ativa_id"`, revalidated on every request: revoked access or a forged id falls back to the first accessible company). All business data — funds, periods, movements — is scoped to an Empresa. Global admins (`Usuario.global_role = PLATFORM_ADMIN`) can switch between companies without a membership.

Accounting-firm users get the pseudo-role `CONTABILIDADE` (`permissions.ROLE_CONTABILIDADE`): they view everything of the linked companies and can post movements (`@company_can_manage_movimentacoes`), but cannot manage users, funds, periods or documents.

### Apps and responsibilities
- **`df/`** — all financial domain models: `Fundo`, `ConfiguracaoDF`, `PeriodoDF`, `HistoricoEmissaoDF`, `MecItem`, and the accounting core: `Conta` (single global chart of accounts, hierarchical), `SaldoInicial`, `Movimentacao`, `LoteImportacaoMovimentacao`, `LinhaDFC`. Balances are **never stored**: they are computed from opening balances + movements (`df/services/saldo_calculo_service.py`). No views live here.
- **`core/`** — all views, forms, templates, and processing logic. Heavy lifter of the application.
- **`usuarios/`** — user model (`Usuario` extends `AbstractUser`), `Empresa`, `Membership`, `Convite`, `EscritorioContabil`/`MembroEscritorio`/`VinculoContabil`/`ConviteEscritorio`, middleware, permission decorators, email invitations, context processor, accounting-firm management screens (`views_escritorios.py`).
- **`cinnamon/`** — Django project config, Celery setup, root URL conf.

### Views pattern
**FBVs only — no CBVs anywhere.** Permission checks via decorators from `usuarios/permissions.py`:
- `@company_can_view_data` — read access
- `@company_can_manage_data` — write access (MASTER/ADMIN)
- `@company_can_manage_fundos` — fund/period CRUD
- `@company_can_manage_movimentacoes` — post movements/opening balances and import spreadsheets (MASTER/ADMIN + the linked accounting firm)
- `@platform_admin_required` — platform-only screens (accounting firms, document panel)

### Financial data flow
1. User selects a **Fundo** (investment fund)
2. **PeriodoDF** records define reporting windows (anual, transitória, encerramento) with deadlines from **ConfiguracaoDF**. Each period has `data_referencia` and `data_anterior` (31/12 of the previous year by default).
3. The chart of accounts is one global tree (`Conta`, loaded from the RFB referential plan: L100A balance sheet + L300A income statement). `natureza`: 1 Ativo, 2 Passivo, 3 PL, 4 Resultado. In this plan the PL (`2.03`) is a child of Passivo (`2`).
4. Accountants/admins record **opening balances** (`SaldoInicial`, per fund + balance-sheet account) and **movements** (`Movimentacao`, signed value per analytic account), manually or by spreadsheet (`core/upload/movimentacao_parser.py` → `core/processing/importacao_movimentacao_service.py`, all-or-nothing, one `LoteImportacaoMovimentacao` per file, undoable). Rules live in `df/services/movimentacao_service.py` (analytic/active/valid account, date after the opening balance, **closed periods are locked**).
5. `saldo_calculo_service` computes balances: result accounts reset on 1/1 and prior years' result flows to the "Lucros/Prejuízos acumulados" accounts (`Conta.papel`) by sign.
6. `core/processing/df_service.gerar_dfs` builds DPF/DRE from the account tree (`demonstrativo_service`, cut by `?nivel=`), DFC from `LinhaDFC` rows (`dfc_arvore_service`), and DMPL from **MEC** (`MecItem`, imported via `core/upload/mec_parser.py`).
7. Export via `core/export/` to Excel (openpyxl) or Word (docxtpl template `media/doc/modelo_df.docx`, variables in `media/doc/variaveis_docx.md`)

Values in statements are in R$ thousands (integers); checks (e.g. Ativo − Passivo − Resultado) use exact values.

### PeriodoDF status lifecycle
Statuses: `nao_iniciada` → `em_andamento` → `finalizada` (or `vencida`)

`atualizar_status_automatico()` in `df/services/periodo_service.py` runs on user interaction (posting/importing movements and opening balances, exporting). To sync stale statuses in bulk on page load, use direct ORM `.update()` calls — see `controle_emissoes` view for the pattern.

### AJAX pattern
All AJAX requests use `fetch` with `X-Requested-With: XMLHttpRequest`. Views detect this via `request.headers.get('X-Requested-With') == 'XMLHttpRequest'` and return `JsonResponse` instead of a redirect/render.

### Async tasks (Celery)
Tasks live in `usuarios/tasks.py`. Email sending uses either Microsoft Graph API (MSAL OAuth2) or SMTP, selected by `EMAIL_SEND_METHOD` in settings. Dev mode uses `CONVITE_ENVIO_SINCRONO=True` to skip Celery. With the default console `EMAIL_BACKEND`, the email is printed in the terminal of whichever process sends it: the **Celery worker** when async, the `runserver` when `CONVITE_ENVIO_SINCRONO=True`. Invite emails are scheduled with `transaction.on_commit`.

## Design system (frontend)

The entire UI uses a custom design system defined in `static/css/main.css`. Key components:

- **`.kpi-card`** + **`.kpi-grid`** — metric cards with colored top bar. Variants: `--blue`, `--green`, `--gray`, `--amber`. Custom variants (`--red`, `--sky`, etc.) are added per-template in `{% block extra_head %}`.
- **`.page-header`** / `.page-header__titles` / `.page-header__title` / `.page-header__subtitle` — standard page title section
- **`.table-ds`** — dark-mode table with hover accent border
- **`.card-header-ds`** — card header with bordeaux left-bar accent
- **`.empty-state`** / `.empty-state-modern`** — empty state placeholders
- **`.form-section`** — form card with icon header

**Gotcha — navbar:** `base/base.html` does NOT include the navbar. Every internal page must start `{% block conteudo %}` with `{% include 'base/navbar.html' %}` (a regression test, `core/tests/test_navbar_e_login.py`, scans the templates; the only exceptions are the login/password-reset/invite-accept public pages).

**Theming:** Dark/light mode via `data-theme` attribute on `<html>` (stored in `localStorage['cinnamon-theme']`). All colors use CSS custom properties (`--surface-1/2/3`, `--text-primary/secondary/muted`, `--border-color`, `--accent-primary`). Light mode overrides are at the bottom of `main.css` under `html[data-theme="light"]`.

**Libraries loaded globally** (in base template): Bootstrap 5.3.3, Bootstrap Icons 1.10.5, jQuery 3.7.0, DataTables 1.13.6.  
**Chart.js 4.4.0** is NOT global — load it per-page in `{% block scripts %}`.

### DataTables pattern
When a table is built dynamically via JS `innerHTML`, destroy any existing instance before re-rendering:
```javascript
let dtInstance = null;
function renderTable(...) {
  if (dtInstance) { dtInstance.destroy(); dtInstance = null; }
  container.innerHTML = `<table id="my-table">...</table>`;
  dtInstance = $("#my-table").DataTable({ ... });
}
```
Use `data-order` on `<td>` elements when cells contain HTML (badges, icons) so DataTables sorts by the raw value rather than rendered text.

## Key URL routes

| URL | View | Notes |
|-----|------|-------|
| `/` | `demonstracao_financeira` | Fund selector + period table |
| `/controle-emissoes/` | `controle_emissoes` | DF period status dashboard |
| `/dre-resultado/<id>/` | `df_resultado` | Multi-statement financial view |
| `/fundos/` | `listar_fundos` | Fund CRUD |
| `/fundos/<id>/periodos/` | `gerenciar_periodos` | Period management |
| `/importar-mec/` | `importar_mec_view` | POST, AJAX-aware |
| `/plano-de-contas/` | `plano_contas_unico` | Single chart of accounts (read for all, edit for platform admin) |
| `/fundos/<id>/movimentacoes/` | `movimentacoes_fundo` | Movements list/CRUD; `.../importar/` imports a spreadsheet |
| `/fundos/<id>/saldos-iniciais/` | `saldos_iniciais_fundo` | Opening balances (CRUD + import) |
| `/periodos/<id>/balancete/` | `balancete_periodo` | Computed trial balance (anterior + movements + adjustments = atual) |
| `/contabilidade/` | `painel_contabilidade` | Accounting-firm users: linked companies and DF progress |
| `/escritorios/` | `escritorios_listar` | Platform admin: firms; `/escritorios/<id>/` team (invites) and linked companies |
| `/escritorios/convite/<token>/` | `aceitar_convite_escritorio` | Public (no auth) |
| `/api/fundo/<id>/periodos/` | `api_periodos_fundo` | JSON API for period table refresh |
| `/convites/aceitar/<token>/` | `aceitar_convite` | Public (no auth) |

## Environment variables (`.env`)

Critical settings beyond Django defaults:
- `EMAIL_SEND_METHOD` — `graph` (Microsoft 365) or `smtp`
- `CONVITE_ENVIO_SINCRONO` — `True` in dev to skip Celery for invite emails
- `CELERY_BROKER_URL` — Redis URL
- MySQL connection vars: `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST`, `DATABASE_PORT`
- `EMAIL_BACKEND` — defaults to the Django console backend (emails are printed, not sent) when `EMAIL_SEND_METHOD=smtp`
- `BASE_URL` — base of the links sent in invites (and printed by `convidar_escritorio`)
