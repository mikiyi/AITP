async (page) => {
  const routes = [
    '/', '/todos', '/lab', '/lab/pipeline', '/lab/prompts', '/lab/sessions', '/lab/records',
    '/projects', '/requirements', '/test-cases', '/bugs', '/sql-analyses',
    '/regressions', '/reports', '/automation/web', '/automation/app', '/automation/api',
    '/automation/performance', '/knowledge', '/knowledge/assistant', '/workbench/entries',
    '/workbench/validation', '/workbench/relations', '/settings', '/settings/ai',
    '/settings/knowledge', '/settings/modules', '/settings/versions', '/settings/templates',
    '/settings/backup', '/settings/automation', '/settings/performance', '/settings/environments', '/settings/home-cards', '/help',
    '/lab/requirement_confirm', '/lab/sessions/88', '/lab/records/81', '/test-cases/102',
    '/bugs/35', '/reports/18', '/knowledge/91', '/knowledge/91/versions', '/not-exist-page'
  ];
  const base = 'http://127.0.0.1:5180';
  const out = [];
  for (const r of routes) {
    const errors = [];
    const onConsole = (msg) => { if (msg.type() === 'error') errors.push(msg.text().slice(0, 300)); };
    const onPageError = (err) => errors.push('pageerror: ' + String(err).slice(0, 300));
    const onFail = (req) => { if (req.url().includes('/api/v1/')) errors.push('reqfail: ' + req.method() + ' ' + req.url().replace(base, '') + ' ' + (req.failure()?.errorText || '')); };
    page.on('console', onConsole);
    page.on('pageerror', onPageError);
    page.on('requestfailed', onFail);
    let status = 0;
    try {
      const resp = await page.goto(base + r, { waitUntil: 'domcontentloaded', timeout: 20000 });
      status = resp ? resp.status() : 0;
      await page.waitForTimeout(1200);
    } catch (e) {
      errors.push('goto: ' + String(e).slice(0, 200));
    }
    const info = await page.evaluate(() => {
      const body = document.body.innerText || '';
      const main = document.querySelector('.app-main, main, .el-main');
      return {
        title: document.title,
        len: body.trim().length,
        hasMain: !!main,
        head: body.trim().slice(0, 160).replace(/\s+/g, ' '),
        emptyStates: document.querySelectorAll('.el-empty').length,
        errorText: /服务器内部错误|Internal Server Error|500|Cannot read|undefined is not|TypeError/.test(body),
        buttons: document.querySelectorAll('button').length,
        navHighlight: (document.querySelector('.el-menu-item.is-active')?.innerText || '').trim(),
        crumbs: Array.from(document.querySelectorAll('.el-breadcrumb__inner')).map((e) => e.textContent.trim()),
      };
    });
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    page.off('requestfailed', onFail);
    out.push({ route: r, status, ...info, errors: errors.slice(0, 6) });
  }
  return JSON.stringify(out, null, 1);
}
