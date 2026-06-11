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

## Architecture overview

### Multi-tenancy model
Every user belongs to one or more **Empresa** (companies) via a **Membership** join table. The active company is stored in `request.empresa_ativa` (injected by `EmpresaAtivaMiddleware` from session key `"empresa_ativa_id"`). All business data — funds, periods, balancetes — is scoped to an Empresa. Global admins (`Usuario.global_role = PLATFORM_ADMIN`) can switch between companies without a membership.

### Apps and responsibilities
- **`df/`** — all financial domain models: `Fundo`, `ConfiguracaoDF`, `PeriodoDF`, `BalanceteItem`, `MecItem`, `MapeamentoContas`, `GrupoGrande`, `GrupoPequeno`, `HistoricoEmissaoDF`. No views live here.
- **`core/`** — all views, forms, templates, and processing logic. Heavy lifter of the application.
- **`usuarios/`** — user model (`Usuario` extends `AbstractUser`), `Empresa`, `Membership`, `Convite`, middleware, permission decorators, email invitations, context processor.
- **`cinnamon/`** — Django project config, Celery setup, root URL conf.

### Views pattern
**FBVs only — no CBVs anywhere.** Permission checks via decorators from `usuarios/permissions.py`:
- `@company_can_view_data` — read access
- `@company_can_manage_data` — write access (import, export)
- `@company_can_manage_fundos` — fund/period CRUD

### Financial data flow
1. User selects a **Fundo** (investment fund)
2. **PeriodoDF** records define reporting periods (trimestral Q1–Q4, anual, transitória, encerramento) with configured deadlines from **ConfiguracaoDF**
3. User imports a **balancete** (Excel trial balance) → parsed by `core/upload/balancete_parser.py` → stored as `BalanceteItem` records
4. User imports **MEC** (Excel investment positions) → `core/upload/mec_parser.py` → stored as `MecItem`
5. Processing services in `core/processing/` compute DRE, DFC, DMPL, DPF statements
6. Export via `core/export/` to Excel (openpyxl) or Word (python-docx)

### PeriodoDF status lifecycle
Statuses: `nao_iniciada` → `em_andamento` → `finalizada` (or `vencida`)

`atualizar_status_automatico()` in `df/services/periodo_service.py` only runs on user interaction (import/export). To sync stale statuses in bulk on page load, use direct ORM `.update()` calls — see `controle_emissoes` view for the pattern.

### AJAX pattern
All AJAX requests use `fetch` with `X-Requested-With: XMLHttpRequest`. Views detect this via `request.headers.get('X-Requested-With') == 'XMLHttpRequest'` and return `JsonResponse` instead of a redirect/render.

### Async tasks (Celery)
Tasks live in `usuarios/tasks.py`. Email sending uses either Microsoft Graph API (MSAL OAuth2) or SMTP, selected by `EMAIL_SEND_METHOD` in settings. Dev mode uses `CONVITE_ENVIO_SINCRONO=True` to skip Celery.

## Design system (frontend)

The entire UI uses a custom design system defined in `static/css/main.css`. Key components:

- **`.kpi-card`** + **`.kpi-grid`** — metric cards with colored top bar. Variants: `--blue`, `--green`, `--gray`, `--amber`. Custom variants (`--red`, `--sky`, etc.) are added per-template in `{% block extra_head %}`.
- **`.page-header`** / `.page-header__titles` / `.page-header__title` / `.page-header__subtitle` — standard page title section
- **`.table-ds`** — dark-mode table with hover accent border
- **`.card-header-ds`** — card header with bordeaux left-bar accent
- **`.empty-state`** / `.empty-state-modern`** — empty state placeholders
- **`.form-section`** — form card with icon header

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
| `/importar-balancete/` | `importar_balancete_view` | POST, AJAX-aware |
| `/importar-mec/` | `importar_mec_view` | POST, AJAX-aware |
| `/api/fundo/<id>/periodos/` | `api_periodos_fundo` | JSON API for period table refresh |
| `/convites/aceitar/<token>/` | `aceitar_convite` | Public (no auth) |

## Environment variables (`.env`)

Critical settings beyond Django defaults:
- `EMAIL_SEND_METHOD` — `graph` (Microsoft 365) or `smtp`
- `CONVITE_ENVIO_SINCRONO` — `True` in dev to skip Celery for invite emails
- `CELERY_BROKER_URL` — Redis URL
- MySQL connection vars: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
