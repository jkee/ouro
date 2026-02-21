// Simple dashboard logic – pulls data from local JSON endpoints or mocks
const budgetWidget = document.getElementById('budget');
const statusWidget = document.getElementById('status');
const tasksWidget = document.getElementById('tasks');
const perfWidget = document.getElementById('perf');

// Mock data – replace with real API calls
const data = {
  budget: '13.68 $',
  status: 'OK',
  tasks: '0 tasks',
  perf: 'Normal',
};

if(budgetWidget) budgetWidget.textContent = data.budget;
if(statusWidget) statusWidget.textContent = data.status;
if(tasksWidget) tasksWidget.textContent = data.tasks;
if(perfWidget) perfWidget.textContent = data.perf;
