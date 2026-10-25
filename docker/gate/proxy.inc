proxy_pass http://app:3000;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header Connection "";
# Identity headers only ever come from the admin path below; strip anything a visitor sends.
proxy_set_header X-Admin-Gate "";
proxy_set_header X-Forwarded-Email "";
proxy_set_header X-Forwarded-User "";
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 300s;
proxy_send_timeout 300s;
