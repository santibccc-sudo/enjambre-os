# enjambre

**Un sistema operativo pequeño y honesto para enjambres de agentes de IA.**

enjambre toma los agentes que ya tienes (Claude Code, Codex, un modelo local con Ollama, una
API en la nube, tus propios scripts) y les da lo que necesita un equipo de procesos para
trabajar junto sin engañarse: un kernel, una cola, reservas que caducan, un filtro de
permisos, un router y una memoria compartida que se puede ver.

[English](README.md)

![Dashboard del enjambre](docs/images/dashboard.png)

```bash
pip install .            # desde un clon; una sola dependencia (PyYAML)
enjambre demo            # abre http://127.0.0.1:8765
```

La demo no necesita modelo ni claves de API. Cuatro agentes de guion hacen una pequeña cadena
editorial: investigar, redactar, revisar y publicar. El crítico falla una vez para que veas un
reintento. El redactor intenta publicar sin permiso y el filtro lo para antes de ejecutarse.
El router explica cada elección.

## Por qué

Un agente suelto ya es fácil. Los enjambres fallan de formas aburridas y caras:

- Un worker muere con la GPU reservada y nadie se entera en diez horas.
- Una tarea se da por hecha y el fichero prometido no existe.
- Un trabajo nocturno parece caído dos tercios del tiempo porque solo corre cada 30 minutos.
- Una regla escrita en el prompt se ignora justo la vez que importa.
- Un paso fallido deja todo lo que depende de él esperando para siempre.

enjambre sale de un enjambre real que funciona día y noche entre una estación de trabajo
alimentada con placas solares y un servidor pequeño en la nube. Cada mecanismo existe porque
uno de esos fallos ocurrió.

## Qué incluye

- **Kernel** en un único fichero SQLite: el estado de cada proceso se deriva de su último
  latido, nunca lo declara el propio proceso.
- **Reservas con caducidad** para GPUs, cuotas o navegadores.
- **Cola duradera**: prioridades, reclamo atómico, claves de idempotencia, renovación para
  trabajos largos, reintentos y dead-letter con motivo.
- **DAG**: las tareas esperan a sus dependencias y reciben sus resultados como *datos*, nunca
  como instrucciones.
- **Prueba de entrega**: el kernel comprueba él mismo ficheros, carpetas y URLs.
- **Filtro de permisos** aplicado en código antes de que ningún agente se ejecute.
- **Router** que mide éxito y latencia, tiene en cuenta coste y energía, y prefiere los agentes
  solares mientras hay sol.
- **Adaptadores** `cli`, `openai` y `scripted`.
- **Servidor MCP** para Claude Code, Codex o cualquier cliente MCP.
- **Grafo de memoria 3D** a partir de notas markdown con `[[enlaces]]`.
- **Dashboard** sin paso de compilación y con CSP estricta.

La documentación completa (configuración, MCP, API y diseño) está en el
[README en inglés](README.md) y en [docs/architecture.md](docs/architecture.md).

## Licencia

MIT. Hecho por GreenAI Network.
