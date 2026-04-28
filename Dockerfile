# --- Backend ---
FROM python:3.11-slim AS backend

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]


# --- Frontend ---
FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN corepack enable && pnpm install --frozen-lockfile 2>/dev/null || npm install

COPY frontend/ .
RUN npm run build


# --- Production ---
FROM python:3.11-slim AS production

# Install Node.js for frontend serving
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend
COPY --from=backend /app/backend /app/backend
COPY --from=backend /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=backend /usr/local/bin /usr/local/bin

# Frontend
COPY --from=frontend-build /app/frontend/.next /app/frontend/.next
COPY --from=frontend-build /app/frontend/public /app/frontend/public
COPY --from=frontend-build /app/frontend/package.json /app/frontend/package.json
COPY --from=frontend-build /app/frontend/node_modules /app/frontend/node_modules

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8001 3001

CMD ["/app/start.sh"]
