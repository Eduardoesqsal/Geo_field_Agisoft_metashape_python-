Abre CMD como administrador.

Para detener el túnel privado:

"C:\Program Files\Tailscale\tailscale.exe" serve --https=443 off

Para volver a ejecutarlo:

"C:\Program Files\Tailscale\tailscale.exe" serve --bg 8001

Para comprobarlo:

"C:\Program Files\Tailscale\tailscale.exe" serve status



Desde CMD como administrador, cuando la app esté corriendo en 127.0.0.1:8001, ejecuta:

"C:\Program Files\Tailscale\tailscale.exe" funnel --bg --https=8443 8001

Si Tailscale muestra un enlace para habilitar Funnel, ábrelo y autorízalo en tu cuenta. Luego consulta la URL pública:

"C:\Program Files\Tailscale\tailscale.exe" funnel status

Usamos 8443 para conservar el túnel privado que ya tienes en 443. Para apagar la URL pública:

"C:\Program Files\Tailscale\tailscale.exe" funnel --https=8443 off

Antes de activarlo: esta app aún no pide contraseña y permite borrar proyectos e iniciar procesos. Funnel la abriría a todo internet. Primero debemos proteger el acceso. Guía oficial de Funnel (https://tailscale.com/docs/reference/tailscale-cli/funnel)