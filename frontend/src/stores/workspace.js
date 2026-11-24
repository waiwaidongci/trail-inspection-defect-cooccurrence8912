import {request, saveFile} from '../api/http.js';
import {session} from './session.js';

const call = (path, body) => request(path, {token:session.token, body});
export const workspace = {
  spec: null,
  datasets: [],
  runs: [],
  async init() { this.spec = await request('/spec'); },
  async refresh() { [this.datasets, this.runs] = await Promise.all([call('/datasets'), call('/runs')]); },
  getDataset: (id, offset = 0) => call(`/datasets/${id}?offset=${offset}`),
  getRun: id => call(`/runs/${id}`),
  upload: data => call('/datasets', data),
  start: data => call('/runs', data),
  review: (id, data) => call(`/runs/${id}/review`, data),
  audit: () => call('/audit'),
  schedules: () => call('/schedules'),
  deliveries: () => call('/deliveries'),
  schedule: data => call('/schedules', data),
  disable: id => call(`/schedules/${id}/disable`, {}),
  supplementBatches: () => call('/supplement/batches'),
  supplement: data => call('/supplement/batches', data),
  ledgers: () => call('/supplement/ledgers'),
  segmentStates: () => call('/supplement/segments'),
  latestChecklist: () => call('/supplement/checklists/latest'),
  checklists: () => call('/supplement/checklists'),
  download: (path, filename) => saveFile(path, session.token, filename),
};
