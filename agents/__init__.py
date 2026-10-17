from .abogado_diablo import AbogadoDelDiablo
from .anomalia import AgenteAnomalia
from .extraccion import AgenteExtraccion
from .fraccionamiento import AgenteFraccionamiento
from .listas import AgenteListas
from .normativa import AgenteNormativo
from .perfil import AgentePerfil
from .redactor import AgenteRedactor
from .transaccional import AgenteTransaccional

# El orden fija la derivación de llaves: agregar agentes nuevos SIEMPRE al final.
ALL_AGENTS = [AgenteTransaccional, AgenteListas, AgenteNormativo, AbogadoDelDiablo, AgenteRedactor, AgentePerfil,
              AgenteFraccionamiento, AgenteAnomalia, AgenteExtraccion]
