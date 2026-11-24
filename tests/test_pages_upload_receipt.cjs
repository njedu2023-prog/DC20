const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')

const workflow = readFileSync(join(__dirname, '../.github/workflows/deploy_dc20_pages.yml'), 'utf8')
const step = workflow.split('      - name: Deploy DC2.0 Pages\n')[1].split('\n      - name:')[0]
const code = step.split('          script: |\n')[1]
const run = new (Object.getPrototypeOf(async function () {}).constructor)(
  'github', 'context', 'core', 'process', 'setTimeout', 'Date', code)

const sha = 'a'.repeat(40)
const context = {repo: {owner: 'njedu2023-prog', repo: 'DC20'}, sha, runId: 100}
const pageUrl = 'https://njedu2023-prog.github.io/DC20/'
const backend = 'b'.repeat(40)
function simulate({id = '14916075787', createError, statuses = ['succeed']} = {}) {
  const requests = [], outputs = []
  let now = 0, reads = 0
  const github = {request: async (route, body) => {
    requests.push([route, body])
    if (route.startsWith('POST ')) {
      if (createError) throw createError
      return {data: {id: backend, page_url: pageUrl}}
    }
    const result = statuses[Math.min(reads++, statuses.length - 1)]
    if (result instanceof Error) throw result
    return {data: {status: result}}
  }}
  const core = {getIDToken: async () => 'test-oidc', setSecret() {}, info() {},
    setOutput: (k,v) => outputs.push([k,v])}
  const promise = run(github, context, core, {env: {PAGES_ARTIFACT_ID: id}},
    (fn, ms) => { now += ms; fn() }, {now: () => now})
  return {requests, outputs, promise}
}

test('creates once with the actual upload receipt and original build SHA, then polls its returned deployment ID', async () => {
  assert.match(step, /PAGES_ARTIFACT_ID: \$\{\{ steps\.pages_upload\.outputs\.artifact_id \}\}/)
  assert.match(step, /retries: 0/)
  const {requests, outputs, promise} = simulate({statuses: [Object.assign(new Error(), {status: 503}), 'syncing_files', 'succeed']})
  await promise
  assert.deepEqual(requests.map(([route]) => route.split(' ')[0]), ['POST', 'GET', 'GET', 'GET'])
  assert.equal(requests[0][1].artifact_id, 14916075787)
  assert.equal(requests[0][1].pages_build_version, sha)
  assert.equal(requests[0][1].oidc_token, 'test-oidc')
  assert.equal(requests[0][1].request.redirect, 'error')
  for (const [route, body] of requests.slice(1)) {
    assert.equal(route, 'GET /repos/{owner}/{repo}/pages/deployments/{deployment_id}')
    assert.equal(body.deployment_id, backend)
  }
  assert.deepEqual(outputs, [['page_url', pageUrl]])
})

test('rejects missing, rounded or external artifact IDs before issuing a request', async () => {
  for (const id of ['', '0', '9007199254740993', 'https://example.invalid/artifact']) {
    const {promise, requests} = simulate({id})
    await assert.rejects(promise, /artifact ID/)
    assert.equal(requests.length, 0)
  }
})

test('never resubmits a POST on conflict or lost response', async () => {
  for (const error of [new Error('connection reset'), Object.assign(new Error(), {status: 400})]) {
    const {promise, requests, outputs} = simulate({createError: error})
    await assert.rejects(promise, /Pages creation/)
    assert.equal(requests.length, 1)
    assert.deepEqual(outputs, [])
  }
})

test('terminal, unknown, permission and timeout outcomes cannot publish', async () => {
  for (const status of ['deployment_failed', 'unrecognized', Object.assign(new Error(), {status: 403}), 'deployment_in_progress']) {
    const {promise, requests, outputs} = simulate({statuses: [status]})
    await assert.rejects(promise)
    assert.equal(requests.filter(([route]) => route.startsWith('POST')).length, 1)
    assert.deepEqual(outputs, [])
  }
})
