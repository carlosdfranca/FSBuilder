import os
from decouple import config
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS').split(',')

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'corsheaders',
    'usuarios', 
    'df',
    'django_celery_beat',  # Celery periodic tasks
    'widget_tweaks',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    "usuarios.middleware.EmpresaAtivaMiddleware",
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'cinnamon.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'core' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'usuarios.context_processors.empresas_contexto',
            ],
        },
    },
]

WSGI_APPLICATION = 'cinnamon.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': config('DATABASE_NAME'),
        'USER': config('DATABASE_USER'),
        'PASSWORD': config('DATABASE_PASSWORD'),
        'HOST': config('DATABASE_HOST'),
        'PORT': config('DATABASE_PORT'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

# URL base para arquivos estáticos
STATIC_URL = '/static/'

# Pasta onde você colocará os arquivos estáticos que **não dependem da app**
STATICFILES_DIRS = [
    BASE_DIR / "static",
]
# Em produção (quando usar collectstatic), os arquivos serão movidos pra cá
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    'http://localhost:8000',
]

# Caminho da URL após login bem-sucedido
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
# URL do login (usada pelo @login_required)
LOGIN_URL = '/login/'

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True


AUTH_USER_MODEL = 'usuarios.Usuario'

# ===== EMAIL CONFIGURATION =====
# Método de envio: 'graph' (Microsoft Graph API OAuth2) ou 'smtp' (tradicional)
EMAIL_SEND_METHOD = config('EMAIL_SEND_METHOD', default='smtp')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@fsbuilder.com')
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Microsoft Graph API (OAuth2) - Recomendado para Microsoft 365
AZURE_TENANT_ID = config('AZURE_TENANT_ID', default='')
AZURE_CLIENT_ID = config('AZURE_CLIENT_ID', default='')
AZURE_CLIENT_SECRET = config('AZURE_CLIENT_SECRET', default='')

# SMTP Configuration (fallback/desenvolvimento)
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')

# ===== CELERY CONFIGURATION =====
# Broker (Redis)
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')

# Serialização
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Timezone
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = False

# Task settings
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutos
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutos

# Retry configuration
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ===== CONVITE CONFIGURATION =====
CONVITE_EXPIRACAO_DIAS = config('CONVITE_EXPIRACAO_DIAS', default=7, cast=int)
CONVITE_MAX_REENVIOS = config('CONVITE_MAX_REENVIOS', default=3, cast=int)
CONVITE_ENVIO_SINCRONO = config('CONVITE_ENVIO_SINCRONO', default=False, cast=bool)  # Para dev/tests
DF_NOTIFICACAO_SINCRONO = config('DF_NOTIFICACAO_SINCRONO', default=False, cast=bool)  # Para dev/tests

# Base URL do site (usado em links de email)
BASE_URL = config('BASE_URL', default='http://localhost:8000')

# ===== CELERY BEAT SCHEDULE =====
# Configuração de tasks periódicas
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'expirar-convites-antigos': {
        'task': 'usuarios.tasks.expirar_convites_antigos',
        'schedule': crontab(hour=3, minute=0),  # Todo dia às 3am
    },
    'verificar-vencimentos-df': {
        'task': 'df.tasks.verificar_vencimentos_df',
        'schedule': crontab(hour=8, minute=0),  # Todo dia às 8am
    },
}


