"""
Celery configuration for FSBuilder project.
"""
import os
from celery import Celery

# Define o módulo de configurações do Django para o 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cinnamon.settings')

app = Celery('cinnamon')

# Usa a string 'cinnamon.settings' para configurar o Celery,
# o namespace='CELERY' significa que todas as config keys relacionadas ao celery
# devem ter o prefixo `CELERY_`.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Carrega tasks de todas as apps registradas no Django.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de debug para testar se o Celery está funcionando."""
    print(f'Request: {self.request!r}')
