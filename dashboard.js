// Базовый скрипт для дашборда
console.log('Dashboard initializing...');

// Плейсхолдеры данных
const budgetData = {
  total: 15.0,
  spent: 1.39,
  remaining: 13.61
};

const perfData = {
  responseTime: '142ms',
  tasksCompleted: 42,
  modelUsage: 'anthropic/claude-sonnet-4.6'
};

const taskData = [
  { id: '6eaaad0e', name: 'Разработка дашборда', status: 'active' },
  { id: 'f5b180f1', name: 'Перевод интерфейса', status: 'completed' }
];

const healthData = {
  drive: 'OK',
  model: 'OK',
  ghIssues: 'Disabled',
  webSearch: 'Error 403'
};

// Рендерим данные на странице
function renderData() {
  // Бюджет
  document.getElementById('budget').innerHTML = `
    <strong>Бюджет OpenRouter:</strong>
    <div>Общий: $${budgetData.total.toFixed(2)}</div>
    <div>Потрачено: $${budgetData.spent.toFixed(2)}</div>
    <div>Остаток: $${budgetData.remaining.toFixed(2)}</div>
  `;
  
  // Производительность
  document.getElementById('perf').innerHTML = `
    <div>Среднее время ответа: ${perfData.responseTime}</div>
    <div>Выполнено задач: ${perfData.tasksCompleted}</div>
    <div>Активная модель: ${perfData.modelUsage}</div>
  `;
  
  // Задачи
  const tasksHTML = taskData.map(task => `
    <div class="task">
      [${task.status === 'active' ? '🟢' : '✅'}] ${task.name} (${task.id})
    </div>
  `).join('');
  document.getElementById('tasks').innerHTML = tasksHTML;
  
  // Здоровье системы
  const healthHTML = Object.entries(healthData).map(([key, value]) => `
    <div>${key}: ${value}</div>
  `).join('');
  document.getElementById('health').innerHTML = healthHTML;
}

// Инициализация
window.onload = () => {
  renderData();
  console.log('Dashboard initialized');
};