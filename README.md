# cinnamon-project
Projeto para empresa Cinnamon

## 🚀 Setup Desenvolvimento Local (Windows)

### Pré-requisitos
- Python 3.10+
- Docker Desktop (para Redis)
- MySQL

### Instalação

1. **Clone o repositório**
```bash
git clone https://github.com/carlosdfranca/cinnamon-project.git
cd cinnamon-project
```

2. **Crie o ambiente virtual**
```bash
python -m venv venv
venv\Scripts\activate
```

3. **Instale dependências**
```bash
pip install -r requirements.txt
```

4. **Configure o .env**
```bash
copy .env.example .env
# Edite o .env com suas configurações
```

5. **Configure Docker (Redis)**
```bash
copy docker-compose.example.yml docker-compose.yml
docker-compose up -d redis
```

6. **Execute migrations**
```bash
python manage.py migrate
```

7. **Crie superusuário**
```bash
python manage.py createsuperuser
```

### Executar o projeto

**Terminal 1 - Django:**
```bash
python manage.py runserver
```

**Terminal 2 - Celery Worker:**
```bash
celery -A cinnamon worker -l info --pool=solo
```

**Terminal 3 (opcional) - Celery Beat:**
```bash
celery -A cinnamon beat -l info
```

Acesse: http://localhost:8000

---

## 🌐 Deploy em Produção (Ubuntu VPS)

Em produção, o Redis é instalado via `apt` (não Docker):

```bash
sudo apt install redis-server
sudo systemctl enable redis-server
```

Celery roda como serviço systemd. Veja documentação completa em `/deploy/`.

---

## 📝 Documentação

- Sistema de Convites: `CONVITES_README.md`
- Análise de Riscos: `ANALISE_RISCOS_CADASTRO_NOVO.md`
- Ações Pré-Deploy: `ACOES_URGENTES_PRE_DEPLOY.md`
