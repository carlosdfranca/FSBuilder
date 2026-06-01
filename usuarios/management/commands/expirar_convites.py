"""
Comando Django para expirar convites manualmente.

Uso:
    python manage.py expirar_convites
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from usuarios.models import Convite


class Command(BaseCommand):
    help = 'Marca convites pendentes expirados como EXPIRED'

    def handle(self, *args, **options):
        agora = timezone.now()
        
        # Busca convites pendentes expirados
        convites_expirados = Convite.objects.filter(
            status=Convite.Status.PENDING,
            expira_em__lt=agora
        )
        
        total = convites_expirados.count()
        
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('✅ Nenhum convite pendente expirado encontrado.')
            )
            return
        
        # Lista os convites antes de expirar
        self.stdout.write(
            self.style.WARNING(f'\n⏰ Encontrados {total} convite(s) expirado(s):\n')
        )
        
        for convite in convites_expirados:
            dias_expirado = (agora - convite.expira_em).days
            self.stdout.write(
                f'  • ID {convite.id}: {convite.email} '
                f'(expirou há {dias_expirado} dia(s))'
            )
        
        # Marca como expirados
        convites_expirados.update(status=Convite.Status.EXPIRED)
        
        self.stdout.write(
            self.style.SUCCESS(f'\n✅ {total} convite(s) marcado(s) como EXPIRED!\n')
        )
        
        # Estatísticas gerais
        stats = {
            'total': Convite.objects.count(),
            'pendentes': Convite.objects.filter(status=Convite.Status.PENDING).count(),
            'aceitos': Convite.objects.filter(status=Convite.Status.ACCEPTED).count(),
            'expirados': Convite.objects.filter(status=Convite.Status.EXPIRED).count(),
            'cancelados': Convite.objects.filter(status=Convite.Status.CANCELLED).count(),
        }
        
        self.stdout.write('📊 Estatísticas atuais:')
        self.stdout.write(f'  Total: {stats["total"]}')
        self.stdout.write(f'  Pendentes: {stats["pendentes"]}')
        self.stdout.write(f'  Aceitos: {stats["aceitos"]}')
        self.stdout.write(
            self.style.WARNING(f'  Expirados: {stats["expirados"]}')
        )
        self.stdout.write(f'  Cancelados: {stats["cancelados"]}')
