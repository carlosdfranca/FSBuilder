# usuarios/middleware.py
from django.utils.deprecation import MiddlewareMixin

from usuarios.models import Empresa
from usuarios.utils.acesso import empresas_acessiveis


class EmpresaAtivaMiddleware(MiddlewareMixin):
    """
    Carrega a empresa ativa a partir da sessão e injeta em request.empresa_ativa.

    Para usuários não-globais (MASTER/ADMIN/MEMBER/VIEWER e Contabilidade):
    - A empresa da sessão só vale se o usuário ainda a alcança (vínculo direto ou escritório
      de contabilidade); acesso revogado ou id adulterado na sessão cai no padrão.
    - Sem empresa válida na sessão, define a primeira empresa acessível (ordem alfabética).

    Para usuários globais (PLATFORM_ADMIN/VIEWER):
    - Precisam selecionar explicitamente via sessão (podem ver múltiplas empresas).
    """
    SESSION_KEY = "empresa_ativa_id"

    def process_request(self, request):
        request.empresa_ativa = None

        # Se não houver usuário autenticado, não faz nada
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return

        user = request.user
        is_global = bool(hasattr(user, "has_global_scope") and user.has_global_scope())

        emp_id = request.session.get(self.SESSION_KEY)
        if emp_id:
            # Uma consulta só: carrega a empresa já restrita ao que o usuário alcança. Se perdeu o acesso
            # (ou a sessão foi adulterada) ela não vem, e cai no padrão abaixo.
            base = Empresa.objects.all() if is_global else empresas_acessiveis(user)
            try:
                empresa = base.only("id", "nome").filter(id=emp_id).first()
            except (ValueError, TypeError):
                empresa = None
            if empresa:
                request.empresa_ativa = empresa
                return
            request.session.pop(self.SESSION_KEY, None)

        # Sem empresa válida na sessão: não-globais recebem a primeira que alcançam
        if not is_global:
            # Empresa completa (como antes, via select_related): evita consultas extras por campo adiado
            empresa = empresas_acessiveis(user).order_by("nome").first()
            if empresa:
                request.empresa_ativa = empresa
                request.session[self.SESSION_KEY] = empresa.id
