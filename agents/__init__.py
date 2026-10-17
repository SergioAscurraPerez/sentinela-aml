from .abogado_diablo import AbogadoDelDiablo
from .listas import AgenteListas
from .normativa import AgenteNormativo
from .perfil import AgentePerfil
from .redactor import AgenteRedactor
from .transaccional import AgenteTransaccional

# El orden fija la derivación de llaves: agregar agentes nuevos SIEMPRE al final.
ALL_AGENTS = [AgenteTransaccional, AgenteListas, AgenteNormativo, AbogadoDelDiablo, AgenteRedactor, AgentePerfil]
