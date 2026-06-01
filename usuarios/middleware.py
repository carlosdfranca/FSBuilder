# usuarios/middleware.py
from django.utils.deprecation import MiddlewareMixin
from usuarios.models import Empresa, Membership

class EmpresaAtivaMiddleware(MiddlewareMixin):
    """
    Carrega a empresa ativa a partir da sessão e injeta em request.empresa_ativa.
    
    Para usuários não-globais (MASTER/ADMIN/MEMBER/VIEWER):
    - Se não houver empresa na sessão, define automaticamente a primeira empresa do vínculo
    - Isso garante que usuários vinculados sempre tenham empresa_ativa definida
    
    Para usuários globais (PLATFORM_ADMIN):
    - Precisam selecionar explicitamente via sessão (podem ver múltiplas empresas)
    """
    SESSION_KEY = "empresa_ativa_id"

    def process_request(self, request):
        request.empresa_ativa = None
        
        # Se não houver usuário autenticado, não faz nada
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return
        
        emp_id = request.session.get(self.SESSION_KEY)
        
        if emp_id:
            # Tem ID na sessão - tenta carregar
            try:
                request.empresa_ativa = Empresa.objects.only("id", "nome").get(id=emp_id)
            except Empresa.DoesNotExist:
                # Limpa sessão se ID inválido
                request.session.pop(self.SESSION_KEY, None)
                emp_id = None
        
        # Se não tem empresa na sessão E usuário não é global
        # Define automaticamente a primeira empresa do vínculo
        if not emp_id and request.user.is_authenticated:
            is_global = hasattr(request.user, 'has_global_scope') and request.user.has_global_scope()
            
            if not is_global:
                # Usuário não-global: pega primeira empresa do vínculo ativo
                membership = Membership.objects.filter(
                    usuario=request.user,
                    is_active=True
                ).select_related('empresa').first()
                
                if membership:
                    request.empresa_ativa = membership.empresa
                    # Salva na sessão para próximas requisições
                    request.session[self.SESSION_KEY] = membership.empresa.id
