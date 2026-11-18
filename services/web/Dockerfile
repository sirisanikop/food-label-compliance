FROM nginx:1.27-alpine
ENV API_KEY=""
COPY services/web/default.conf.template /etc/nginx/templates/default.conf.template
COPY services/web/index.html /usr/share/nginx/html/index.html
