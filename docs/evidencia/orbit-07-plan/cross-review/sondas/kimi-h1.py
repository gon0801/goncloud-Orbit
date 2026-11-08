import inspect
from decimal import Decimal
from test_optimizer_caso import cortes
from app import apply_cola
from app.optimizer import goals
from app.optimizer.bid import POLITICA_PAUSE_ECONOMICA, decide_pause

m2 = open('plans/orbit-07-motor-decisiones.md', encoding='utf-8').read().split(
    '## Resolver la meta y el límite económico del mismo producto (M2)'
)[1].split('## Compartir el aprendizaje')[0]
print('M2 asigna apply_cola:', 'app/apply_cola.py' in m2)
print('revalidacion llama:', inspect.getsource(
    apply_cola._target_pause_vigente
).split('return g.cascada_target_acos_con_procedencia')[1].splitlines()[1].strip())
meta, origen = goals.cascada_target_acos_con_procedencia(
    None, None, {}, None, 'amazon_mx', Decimal('20')
)
print('target plataforma:', meta, origen, '| target M2:', Decimal('8') * Decimal('0.5'))
evidencia = cortes(orders=1, clicks=120, cost='5000', revenue='10000')
for target in ('4', '20'):
    r = decide_pause(
        platform='amazon_mx', bids=None, cortes=evidencia,
        target_acos_pct=Decimal(target), umbral_pause=100,
        policy_version=POLITICA_PAUSE_ECONOMICA,
    )
    print(f'target={target}% kind={r.kind} motivo={r.motivo}')
