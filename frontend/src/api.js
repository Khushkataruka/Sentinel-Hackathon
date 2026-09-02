// One place that knows how to talk to the two services.
//
// The user header is the stub authentication both services accept. When real
// authentication lands, this is the only file that changes on the client.

const USER_HEADER = 'X-Sentinel-User'

function currentUser() {
  return localStorage.getItem('sentinel.user') || 'admin'
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      [USER_HEADER]: currentUser(),
      ...(options.headers || {})
    }
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${body}`)
  }
  return response.status === 204 ? null : response.json()
}

export const api = {
  // map
  cameras: () => request('/map/cameras'),
  coverage: () => request('/map/coverage'),

  // search
  searchRegistration: (body) =>
    request('/search/registration', { method: 'POST', body: JSON.stringify(body) }),
  searchDescription: (body) =>
    request('/search/description', { method: 'POST', body: JSON.stringify(body) }),
  routesForSearch: (id) => request(`/searches/${id}/routes`),
  route: (id) => request(`/routes/${id}`),

  // alerts
  alerts: (params = '') => request(`/alerts${params}`),
  decideAlert: (id, body) =>
    request(`/alerts/${id}/decide`, { method: 'POST', body: JSON.stringify(body) }),

  // violations
  violationTypes: () => request('/violations/types'),
  violations: (params = '') => request(`/violations${params}`),
  reviewViolation: (id, body) =>
    request(`/violations/${id}/review`, { method: 'POST', body: JSON.stringify(body) }),

  // traffic
  trafficState: (params = '') => request(`/traffic/state${params}`),
  trafficCounts: (params = '') => request(`/traffic/counts${params}`),
  congestion: () => request('/traffic/congestion'),

  // evidence
  evidence: (readId) => request(`/evidence/${readId}`),
  frames: (params) => request(`/frames${params}`),
  mediaUrl: (kind, ref) => `/media/${kind}/${ref}`,

  // watchlist
  watchlist: () => request('/watchlist'),
  addWatchlist: (body) =>
    request('/watchlist', { method: 'POST', body: JSON.stringify(body) }),

  // admin, on the registry
  adapters: () => request('/adapters'),
  queueDepth: () => request('/queues/depth'),
  camerasNeedingSurvey: () => request('/cameras/needing-survey')
}
