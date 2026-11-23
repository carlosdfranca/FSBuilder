"""
Cria um convite para a equipe de um escritório de contabilidade e imprime o link de cadastro.

Por padrão NÃO envia e-mail: é só pegar o link no terminal e entregar à pessoa (útil em dev, ou
quando o e-mail não está configurado). Use --enviar para mandar também o e-mail.

Exemplos:
    python manage.py convidar_escritorio maria@contabil.com --escritorio "Contábil X"
    python manage.py convidar_escritorio maria@contabil.com --escritorio "Contábil X" --papel ADMIN
    python manage.py convidar_escritorio maria@contabil.com --escritorio "Novo Escritório" --criar-escritorio
    python manage.py convidar_escritorio maria@contabil.com --escritorio "Contábil X" --enviar
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from usuarios.models import EscritorioContabil, MembroEscritorio, Usuario
from usuarios.services import convite_escritorio_service as svc
from usuarios.services.convite_escritorio_service import ConviteEscritorioError


class Command(BaseCommand):
    help = "Cria um convite para a equipe de um escritório de contabilidade e mostra o link de cadastro."

    def add_arguments(self, parser):
        parser.add_argument("email", help="E-mail da pessoa convidada")
        parser.add_argument("--escritorio", required=True, help="Nome do escritório (sem diferenciar maiúsculas)")
        parser.add_argument(
            "--papel", default=MembroEscritorio.Role.MEMBRO, choices=MembroEscritorio.Role.values,
            help="Papel na equipe (padrão: MEMBRO)",
        )
        parser.add_argument("--por", help="Usuário (admin da plataforma) que convida; padrão: o primeiro admin")
        parser.add_argument("--criar-escritorio", action="store_true", help="Cria o escritório se ele ainda não existir")
        parser.add_argument("--cnpj", help="CNPJ do escritório, usado só com --criar-escritorio")
        parser.add_argument("--enviar", action="store_true", help="Envia também o e-mail do convite (padrão: só mostra o link)")

    def handle(self, *args, **opts):
        escritorio = self._escritorio(opts)
        por = self._quem_convida(opts["por"])

        try:
            convite = svc.criar_convite(
                escritorio=escritorio, email=opts["email"], role=opts["papel"],
                convidado_por=por, enviar_email=opts["enviar"],
            )
        except PermissionDenied as exc:
            raise CommandError(str(exc) or "Sem permissão para convidar.") from exc
        except ConviteEscritorioError as exc:
            raise CommandError(str(exc)) from exc

        link = convite.get_link_aceite(getattr(settings, "BASE_URL", "http://localhost:8000"))
        self.stdout.write(self.style.SUCCESS(f"\nConvite criado para {convite.email} no escritório {escritorio.nome}."))
        self.stdout.write(f"Papel:   {convite.get_role_display()}")
        self.stdout.write(f"Expira:  {convite.expira_em:%d/%m/%Y %H:%M}")
        self.stdout.write(f"E-mail:  {'enviado' if opts['enviar'] else 'não enviado (use --enviar para mandar)'}")
        self.stdout.write(f"\nLink de cadastro:\n  {self.style.HTTP_INFO(link)}\n")
        if svc.existe_conta_com_email(convite.email):
            self.stdout.write(self.style.WARNING(
                "Atenção: este e-mail já tem conta. Pelo link a pessoa entra nela para aceitar (não cria outra)."
            ))

    def _escritorio(self, opts) -> EscritorioContabil:
        nome = opts["escritorio"].strip()
        encontrado = EscritorioContabil.objects.filter(nome__iexact=nome).first()
        if encontrado:
            return encontrado
        if not opts["criar_escritorio"]:
            existentes = ", ".join(EscritorioContabil.objects.values_list("nome", flat=True)) or "(nenhum)"
            raise CommandError(
                f'Escritório "{nome}" não encontrado. Existentes: {existentes}. '
                f"Use --criar-escritorio para cadastrá-lo."
            )
        criado = EscritorioContabil.objects.create(nome=nome, cnpj=(opts["cnpj"] or "").strip() or None)
        self.stdout.write(self.style.SUCCESS(f"Escritório \"{criado.nome}\" criado."))
        return criado

    def _quem_convida(self, username) -> Usuario:
        if username:
            usuario = Usuario.objects.filter(username=username, is_active=True).first()
            if usuario is None:
                raise CommandError(f'Usuário "{username}" não encontrado ou inativo.')
            return usuario
        usuario = (
            Usuario.objects
            .filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(global_role=Usuario.GlobalRole.PLATFORM_ADMIN))
            .order_by("id")
            .first()
        )
        if usuario is None:
            raise CommandError("Nenhum administrador da plataforma encontrado. Informe um com --por <usuario>.")
        return usuario
