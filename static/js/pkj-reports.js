/* Wykresy raportów KJ Prestiż (Chart.js): jedna oś, cienkie słupki, tabela z danymi pod wykresem.
   FPY, koszt i czas: jedna seria słupków. Pareto: słupki udziału i linia udziału narastającego na wspólnej osi 0 do 100%. */
(function () {
  function init() {
    var holder = document.getElementById('pkj-chart-data'), canvas = document.getElementById('pkj-chart');
    if (!holder || !canvas || !window.Chart) return;
    var d = JSON.parse(holder.textContent);
    var dark = document.body.classList.contains('dark') || document.documentElement.getAttribute('data-theme') === 'dark';
    var ink = getComputedStyle(document.body).color || '#334155';
    var grid = 'rgba(128,128,128,.18)';
    var barColor = dark ? '#5bb5d3' : '#1a6e8a', lineColor = dark ? '#fb923c' : '#c2410c';
    var surface = dark ? '#1e293b' : '#ffffff';

    function wrap(text, width) {
      var words = String(text).split(' '), lines = [], cur = '';
      words.forEach(function (w) {
        if ((cur + ' ' + w).trim().length > width && cur) { lines.push(cur); cur = w; } else { cur = (cur + ' ' + w).trim(); }
      });
      if (cur) lines.push(cur);
      return lines;
    }
    function fmt(v) { return (Math.round(v * 10) / 10).toString().replace('.', ',') + (d.suffix || ''); }

    var datasets = [{
      type: 'bar', label: d.name, data: d.values, backgroundColor: barColor, borderRadius: { topLeft: 4, topRight: 4 },
      maxBarThickness: 36, order: 2
    }];
    if (d.type === 'pareto') {
      datasets.push({
        type: 'line', label: 'Udział narastający', data: d.cumulative, borderColor: lineColor, backgroundColor: lineColor,
        borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: lineColor, pointBorderColor: surface,
        pointBorderWidth: 2, tension: 0, order: 1
      });
    }
    new Chart(canvas, {
      data: { labels: d.labels.map(function (l) { return wrap(l, 14); }), datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: datasets.length > 1, labels: { color: ink, usePointStyle: true, boxWidth: 8 } },
          tooltip: {
            callbacks: {
              title: function (items) { return d.labels[items[0].dataIndex]; },
              label: function (c) { return ' ' + c.dataset.label + ': ' + fmt(c.parsed.y); },
              afterBody: function (items) { return d.tips && d.tips[items[0].dataIndex] ? d.tips[items[0].dataIndex] : ''; }
            }
          }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: ink, maxRotation: 0, autoSkip: false, font: { size: 11 } } },
          y: {
            beginAtZero: true, max: d.max || undefined, grid: { color: grid },
            ticks: { color: ink, callback: function (v) { return v + (d.suffix || '').replace(' PLN', ''); } }
          }
        }
      }
    });
  }
  if (document.readyState === 'complete') init(); else window.addEventListener('load', init);
})();
