import path from 'path'
import http from 'node:http'
import https from 'node:https'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'

function createApiHttpProxyPlugin(target: string, proxy: string): Plugin {
  const targetUrl = new URL(target)
  const proxyUrl = new URL(proxy)
  const proxyTransport = proxyUrl.protocol === 'https:' ? https : http

  return {
    name: 'opsmesh-api-http-proxy',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (!request.url?.startsWith('/api')) {
          next()
          return
        }

        const upstreamUrl = new URL(request.url, targetUrl)
        const headers = { ...request.headers, host: upstreamUrl.host }
        delete headers.connection

        const proxyRequest = proxyTransport.request(
          {
            hostname: proxyUrl.hostname,
            port: proxyUrl.port || (proxyUrl.protocol === 'https:' ? 443 : 80),
            method: request.method,
            path: upstreamUrl.toString(),
            headers,
          },
          (proxyResponse) => {
            response.writeHead(proxyResponse.statusCode ?? 502, proxyResponse.headers)
            proxyResponse.pipe(response)
          },
        )

        proxyRequest.on('error', () => {
          if (!response.headersSent) {
            response.statusCode = 502
            response.setHeader('content-type', 'application/json')
            response.end(JSON.stringify({ detail: 'API proxy unavailable' }))
          } else {
            response.destroy()
          }
        })

        request.pipe(proxyRequest)
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
  const apiHttpProxy = env.VITE_API_HTTP_PROXY

  return {
    plugins: [
      tanstackRouter({
        target: 'react',
        autoCodeSplitting: true,
      }),
      react(),
      tailwindcss(),
      ...(apiHttpProxy ? [createApiHttpProxyPlugin(apiTarget, apiHttpProxy)] : []),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      ...(apiHttpProxy
        ? {}
        : {
            proxy: {
              '/api': {
                target: apiTarget,
                changeOrigin: true,
              },
            },
          }),
    },
  }
})
