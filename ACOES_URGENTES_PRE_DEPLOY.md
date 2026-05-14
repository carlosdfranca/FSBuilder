# ⚡ Ações URGENTES Pré-Deploy

**Status:** 🔴 **NÃO FAZER DEPLOY ATÉ COMPLETAR ESTE CHECKLIST**

---

## 1. 🔐 SECRET_KEY (CRÍTICO)

**Arquivo:** `.env` linha 4

**Problema atual:**
```env
SECRET_KEY=django-insecure-change-this-in-production-use-50-plus-random-characters
```

**Ação:**
```bash
# 1. Gerar nova chave
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# 2. Copiar resultado e colar no .env
SECRET_KEY=<cole-aqui-a-chave-gerada-50-chars>

# 3. Verificar que mudou
grep SECRET_KEY .env
```

**Por que é crítico:** Permite falsificação de sessões, bypass de CSRF, comprometimento total de segurança.

---

## 2. 🔑 Senha do Banco de Dados (CRÍTICO)

**Arquivo:** `.env` linhas 10-12

**Problema atual:**
```env
DATABASE_PASSWORD=cocodochico  # ❌ Senha fraca
```

**Ação:**
```bash
# 1. Gerar senha forte (ou usar gerador online)
openssl rand -base64 32

# 2. Criar novo usuário MySQL
mysql -u root -p
```

```sql
-- No MySQL
CREATE USER 'fsbuilder_app'@'localhost' IDENTIFIED BY '<senha-forte-gerada>';
GRANT ALL PRIVILEGES ON fsbuilder.* TO 'fsbuilder_app'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

```env
# 3. Atualizar .env
DATABASE_USER=fsbuilder_app
DATABASE_PASSWORD=<senha-forte-aqui>
```

```bash
# 4. Testar conexão
python manage.py check
```

**Por que é crítico:** Invasor pode acessar/deletar todos os dados da aplicação.

---

## 3. 📧 Email Backend (CRÍTICO)

**Arquivo:** `.env` linha 21

**Problema atual:**
```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend  # ❌ Apenas console
```

**Sistema de convites NÃO FUNCIONA sem email real!**

### Opção A: Gmail (Mais fácil para teste)

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=seu-email@gmail.com
EMAIL_HOST_PASSWORD=xxxx-xxxx-xxxx-xxxx
DEFAULT_FROM_EMAIL=noreply@fsbuilder.com
```

**Como pegar senha de app do Gmail:**
1. Ir em https://myaccount.google.com/security
2. Ativar "Verificação em duas etapas"
3. Ir em "Senhas de app"
4. Gerar senha para "Mail" + "Windows Computer"
5. Copiar senha de 16 dígitos

### Opção B: SendGrid (Recomendado produção)

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.xxxxxxxxxxxxxxxxxxxxxxxx
DEFAULT_FROM_EMAIL=noreply@fsbuilder.com
```

**Como configurar SendGrid:**
1. Criar conta em https://sendgrid.com (grátis 100 emails/dia)
2. Settings → API Keys → Create API Key
3. Copiar chave e usar como `EMAIL_HOST_PASSWORD`

**Testar envio:**
```bash
python manage.py shell
```

```python
from django.core.mail import send_mail
send_mail(
    'Teste',
    'Funcionou!',
    'noreply@fsbuilder.com',
    ['seu-email@teste.com'],
    fail_silently=False,
)
# Deve retornar 1 (sucesso)
```

---

## 4. 🍪 Cookies de Sessão (CRÍTICO)

**Arquivo:** `cinnamon/settings.py` linhas 150-153

**Problema atual:**
```python
SESSION_COOKIE_SECURE = True  # ❌ Impede login em localhost HTTP
CSRF_COOKIE_SECURE = True     # ❌ Impede login em localhost HTTP
```

**Ação:**
Substituir linhas 150-153 por:

```python
# Cookies seguros apenas em produção (HTTPS)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG

# Em desenvolvimento: False (permite HTTP)
# Em produção: True (força HTTPS)
```

**Testar:**
```bash
# Deve conseguir fazer login normalmente
python manage.py runserver
# Acessar http://localhost:8000/login/
```

---

## 5. ✅ Validação de Username Único

**Arquivo:** `usuarios/forms.py` - classe `AceitarConviteForm`

**Adicionar validação:**

```python
# Adicionar este método na classe AceitarConviteForm (linha ~90)

def clean_username(self):
    """Valida se username está disponível."""
    username = self.cleaned_data.get('username')
    if username:
        # Verifica se já existe
        if Usuario.objects.filter(username__iexact=username).exists():
            raise ValidationError(
                "Este nome de usuário já está em uso. Escolha outro."
            )
    return username
```

---

## 6. 🚫 Verificar .gitignore

**Ação:**
```bash
# Verificar se .env está no .gitignore
cat .gitignore | grep .env

# Se não estiver, adicionar
echo ".env" >> .gitignore

# Se .env já foi commitado antes (PERIGO!), remover do histórico
git rm --cached .env
git commit -m "Remove .env from repository"
```

**Verificar histórico do Git:**
```bash
# Ver se .env aparece em commits antigos
git log --all --full-history -- .env

# Se aparecer, considerar trocar TODAS as senhas/keys
```

---

## 7. ☁️ Configurar Celery + Redis

**Verificar se está rodando:**

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery Worker
celery -A cinnamon worker -l info

# Terminal 3: Celery Beat (task periódica)
celery -A cinnamon beat -l info
```

**Se não estiver instalado:**
```bash
pip install celery redis django-celery-beat
```

**Testar envio de email assíncrono:**
```bash
python manage.py shell
```

```python
from usuarios.tasks import enviar_email_convite_async
# Criar um convite de teste e testar
# ... (verificar se task é processada)
```

---

## ✅ Checklist Final (Antes de Deploy)

Marque cada item ao completar:

### Segurança
- [ ] SECRET_KEY alterada (50+ caracteres aleatórios)
- [ ] Senha do banco forte (20+ caracteres)
- [ ] `.env` no `.gitignore`
- [ ] `.env` NÃO está no histórico do Git
- [ ] `SESSION_COOKIE_SECURE = not DEBUG`
- [ ] `CSRF_COOKIE_SECURE = not DEBUG`

### Email
- [ ] Email backend configurado (Gmail/SendGrid)
- [ ] Teste de envio de email funcionando
- [ ] Variável `DEFAULT_FROM_EMAIL` configurada

### Celery
- [ ] Redis instalado e rodando
- [ ] Celery worker rodando
- [ ] Celery beat rodando
- [ ] Teste de task assíncrona funcionando

### Banco de Dados
- [ ] Migrations aplicadas (`python manage.py migrate`)
- [ ] Usuário MASTER criado via Django Admin
- [ ] Backup do banco configurado

### Código
- [ ] Validação de username único adicionada
- [ ] `python manage.py check` sem erros
- [ ] Login funcionando normalmente

### Deploy
- [ ] `DEBUG = False` no .env de produção
- [ ] `ALLOWED_HOSTS` com domínio real
- [ ] HTTPS/SSL configurado no servidor
- [ ] Arquivos estáticos coletados (`collectstatic`)

---

## 🧪 Script de Teste Final

Execute este script para validar configurações:

```python
# test_config.py
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cinnamon.settings')
django.setup()

from django.conf import settings
from django.core.mail import send_mail
from usuarios.models import Usuario, Empresa
import sys

print("=" * 50)
print("TESTE DE CONFIGURAÇÃO PRÉ-DEPLOY")
print("=" * 50)

# 1. SECRET_KEY
print("\n1. SECRET_KEY")
if settings.SECRET_KEY == "django-insecure-change-this-in-production-use-50-plus-random-characters":
    print("❌ FALHA: SECRET_KEY ainda é a padrão!")
    sys.exit(1)
elif len(settings.SECRET_KEY) < 50:
    print("⚠️  AVISO: SECRET_KEY muito curta (< 50 chars)")
else:
    print("✅ OK: SECRET_KEY configurada")

# 2. DEBUG
print("\n2. DEBUG MODE")
if settings.DEBUG:
    print("⚠️  AVISO: DEBUG=True (OK para dev, mudar para False em produção)")
else:
    print("✅ OK: DEBUG=False")

# 3. EMAIL
print("\n3. EMAIL BACKEND")
if 'console' in settings.EMAIL_BACKEND.lower():
    print("❌ FALHA: Email backend ainda é console!")
    print("   Sistema de convites NÃO funcionará!")
    sys.exit(1)
else:
    print(f"✅ OK: Email backend = {settings.EMAIL_BACKEND}")
    
    # Tentar enviar email de teste
    try:
        result = send_mail(
            'Teste FSBuilder',
            'Email de teste funcionando!',
            settings.DEFAULT_FROM_EMAIL,
            ['teste@example.com'],
            fail_silently=False,
        )
        print("✅ OK: Envio de email funcionando")
    except Exception as e:
        print(f"❌ FALHA: Erro ao enviar email: {e}")
        sys.exit(1)

# 4. BANCO DE DADOS
print("\n4. BANCO DE DADOS")
try:
    count = Usuario.objects.count()
    print(f"✅ OK: Conexão com banco funcionando ({count} usuários)")
except Exception as e:
    print(f"❌ FALHA: Erro de conexão: {e}")
    sys.exit(1)

# 5. CELERY
print("\n5. CELERY/REDIS")
try:
    from celery import current_app
    print(f"✅ OK: Celery configurado (broker: {settings.CELERY_BROKER_URL})")
except Exception as e:
    print(f"❌ FALHA: Erro Celery: {e}")
    sys.exit(1)

print("\n" + "=" * 50)
print("✅ TODOS OS TESTES PASSARAM!")
print("Sistema pronto para deploy.")
print("=" * 50)
```

**Executar:**
```bash
python test_config.py
```

---

## 📞 Suporte

Se algum teste falhar ou tiver dúvidas:
1. Revisar o arquivo completo `ANALISE_RISCOS_CADASTRO_NOVO.md`
2. Verificar logs de erro
3. Consultar documentação oficial do Django

---

**IMPORTANTE:** NÃO fazer deploy até TODOS os itens do checklist estarem marcados! ✅
