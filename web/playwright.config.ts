import {defineConfig} from '@playwright/test';
const python=process.platform==='win32'?'..\\.venv\\Scripts\\python.exe':'../.venv/bin/python';
export default defineConfig({testDir:'./tests',timeout:120000,workers:1,use:{baseURL:'http://127.0.0.1:5173',trace:'retain-on-failure'},webServer:[
  {command:`${python} -m freshpilot.api`,url:'http://127.0.0.1:8000/docs',reuseExistingServer:!process.env.CI,timeout:60000,env:{FRESHPILOT_LLM:'off'}},
  {command:'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:!process.env.CI,timeout:60000}
]});
