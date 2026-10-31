# P310 frontend

React + TypeScript + Vite dashboard for the P310 backend.

    npm install
    npm run dev      # http://127.0.0.1:5173, proxies /api -> http://127.0.0.1:8000
    npm run build

Set VITE_BACKEND_URL to point the dev proxy at another backend, or VITE_API_URL to call it directly.
