import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, normalize } from 'node:path'

const host = process.env.HOST ?? '0.0.0.0'
const port = Number(process.env.PORT ?? '5173')
const root = join(import.meta.dirname, 'dist')
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
}

function safeAssetPath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0])
  const relative = normalize(decoded).replace(/^([/\\])+/, '')
  const candidate = join(root, relative)
  return candidate.startsWith(root) ? candidate : null
}

const server = createServer(async (request, response) => {
  if (request.url === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    response.end('{"status":"ok","service":"fixflow-frontend"}')
    return
  }

  const candidate = safeAssetPath(request.url ?? '/')
  let asset = candidate
  try {
    if (!asset || !(await stat(asset)).isFile()) asset = join(root, 'index.html')
  } catch {
    asset = join(root, 'index.html')
  }
  const contentType = contentTypes[extname(asset)] ?? 'application/octet-stream'
  response.writeHead(200, {
    'Content-Type': contentType,
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
  })
  createReadStream(asset).pipe(response)
})

server.listen(port, host)
