-- F.1 TEMPORADA 01: antes de desplegar el bloque F no existe ninguna decision
-- `resume` del motor temporada (las reactivaciones nacen con este bloque).
SELECT count(*)
  FROM decision
 WHERE kind = 'resume'
   AND inputs->>'motor' = 'temporada'
