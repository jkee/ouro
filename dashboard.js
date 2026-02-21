// Dashboard logic – fetches data from GitHub and displays budget info
const budgetWidget = document.getElementById('budget');
const statusWidget = document.getElementById('status');
const tasksWidget = document.getElementById('tasks');
const perfWidget = document.getElementById('perf');

// Mock data placeholder – will be replaced after fetch
const data = {
  budget: '13.68 $',
  status: 'OK',
  tasks: '0',
  perf: 'Normal',
};

function updateWidgets() {
  if(budgetWidget) budgetWidget.textContent = data.budget;
  if(statusWidget) statusWidget.textContent = data.status;
  if(tasksWidget) tasksWidget.textContent = data.tasks;
  if(perfWidget) perfWidget.textContent = data.perf;
}

// Fetch repository stats from GitHub API
async function fetchRepoStats() {
  try {
    const resp = await fetch('https://api.github.com/repos/Axelsage/ouroboros');
    if(!resp.ok) throw new Error('GitHub API error');
    const repo = await resp.json();
    data.tasks = repo.open_issues_count;
    data.status = repo.status || 'OK';
    // Performance: compute commits last 7 days via events? Simplify to stars
    data.perf = `⭐ ${repo.stargazers_count}`;
  } catch(e) {
    data.status = 'Error fetching repo data';
  }
  updateWidgets();
}

// Fetch budget from OpenRouter via local endpoint if available
async function fetchBudget() {
  try {
    const resp = await fetch('/api/budget');
    if(resp.ok){
      const json = await resp.json();
      data.budget = `${json.remaining} $`;
    }
  } catch(e) { /* ignore */ }
}

(async () => {
  fetchBudget();
  fetchRepoStats();
})();
