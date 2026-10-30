"""Offline browser acceptance checks, using a locally installed Chromium/Edge.

Run python3 test_browser.py [--browser /path/to/chromium].
No Python test dependencies are needed. On WSL, Windows Edge is supported.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import diff_review as dr

CHECKS = r'''
(async()=>{
 const assert=(ok,message)=>{if(!ok)throw Error(message)};
 const click=selector=>document.querySelector(selector).click();
 const summary=()=>document.querySelector('#commentsBox').value;
 const count=selector=>document.querySelectorAll(selector).length;
 const result=document.createElement('pre');result.id='testResults';
 document.body.append(result);
 try{
  assert(count('.diffcard')===2,'file cards');
  assert(count('.commentcard')===3,'initial imported comments');
  assert(!globalThis.pwned&&count('img')===0,'HTML injection');
  if(location.search==='?reload=1'){
   assert(!summary().includes('Unchanged line note'),'reload restores initial comments');
   result.textContent=JSON.stringify({passed:true,reload:true});return;
  }
  assert(!document.querySelector('#wrapControl').hidden,'overflow reveals wrap toggle');
  assert(!document.querySelector('#wrapToggle').checked,'wrapping off by default');
  assert([...document.querySelectorAll('.codecell')].some(c=>c.scrollWidth>c.clientWidth+1),'long code initially overflows');
  const initialSummary=summary();
  click('#wrapToggle');
  assert(document.querySelector('#wrapToggle').checked,'enable wrapping');
  assert([...document.querySelectorAll('.codecell')].every(c=>c.scrollWidth<=c.clientWidth+1),'wrapped lines fit code column');
  assert(summary()===initialSummary,'wrap preserves comments');
  click('#wrapToggle');
  assert([...document.querySelectorAll('.codecell')].some(c=>c.scrollWidth>c.clientWidth+1),'disable wrapping restores scroll');
  assert(document.querySelector('#f0 .add .intra').textContent===', na.rm = TRUE','Unicode character highlight offsets');
  click('#themeToggle');
  const firstTheme=document.documentElement.dataset.theme;
  assert(['light','dark'].includes(firstTheme),'theme toggle');
  click('#themeToggle');
  assert(document.documentElement.dataset.theme!==firstTheme,'toggle back');
  setTheme(true);
  assert(getComputedStyle(document.documentElement).colorScheme==='dark','native dark theme');
  assert(getComputedStyle(document.querySelector('.codecell')).color==='rgb(245, 242, 233)' || getComputedStyle(document.querySelector('.codecell')).color==='rgb(255, 154, 138)','high-contrast dark code');
  assert(summary()===initialSummary,'theme preserves comments');
  setTheme(false);
  assert(getComputedStyle(document.documentElement).colorScheme==='light','native light theme');
  assert(summary().includes('old line 1'),'deletion anchor');
  assert(summary().includes('[unmatched anchor]'),'unmatched comment');
  click('[data-expand="f0"]');
  assert(document.querySelector('#f0 .add .intra').textContent===', na.rm = TRUE','whole-file character highlights');
  click('[data-compose="f0:new:3"]');
  document.querySelector('form.composer textarea').value='Unchanged line note';
  document.querySelector('form.composer').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  assert(summary().includes('new line 3')&&summary().includes('Author: You'),'whole-file line comment');
  click('[data-expand="f0"]');
  assert(summary().includes('Unchanged line note'),'whole-file comment survives toggle');
  const filter=document.querySelector('[data-type="MD"]');filter.checked=false;
  filter.dispatchEvent(new Event('change',{bubbles:true}));
  assert(count('.diffcard')===1&&count('#fileNav .filelink')===1,'type filtering');
  assert(document.querySelector('#wrapControl').hidden,'hide wrap toggle without overflowing code');
  assert(summary().includes('</script>'),'hidden comments preserved');
  click('#contextToggle');assert(count('.diffrow.ctx')===0,'hide context');
  click('#contextToggle');assert(count('.diffrow.ctx')===1,'restore context');
  click('[data-compose="f0:new:1"]');
  document.querySelector('form.composer textarea').value='Added in browser';
  document.querySelector('form.composer').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  assert(summary().includes('Added in browser'),'comment adds to summary');
  [...document.querySelectorAll('.commentcard')].find(el=>el.textContent.includes('Added in browser')).querySelector('button').click();
  assert(!summary().includes('Added in browser'),'comment deletion');
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{globalThis.copied=text}}});
  click('#exportBtn');await Promise.resolve();
  assert(globalThis.copied===summary(),'Clipboard API exact text');
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
  const original=document.execCommand.bind(document);
  document.execCommand=command=>{
   const box=document.querySelector('#commentsBox');
   globalThis.fallbackText=box.value.slice(box.selectionStart,box.selectionEnd);
   globalThis.fallbackResult=original(command);return globalThis.fallbackResult;
  };
  click('#exportBtn');await Promise.resolve();
  assert(globalThis.fallbackText===summary(),'fallback selects exact text');
  document.execCommand=()=>false;click('#exportBtn');await Promise.resolve();
  const box=document.querySelector('#commentsBox');
  assert(box.selectionEnd-box.selectionStart===box.value.length,'blocked copy keeps selection');
  assert(document.querySelector('#toast').textContent.includes('Ctrl+C'),'manual-copy explanation');
  assert(performance.getEntriesByType('resource').length===0,'offline resources');
  assert(count('a[download]')===0,'no downloads');
  location.search='?reload=1';
 }catch(error){result.textContent=JSON.stringify({passed:false,error:error.message})}
})();
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    args = parser.parse_args()
    windows_edge = '/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    browser = args.browser or shutil.which('chromium') or shutil.which('google-chrome')
    if not browser and Path(windows_edge).exists():
        browser = windows_edge
    if not browser:
        parser.error('A Chromium/Edge executable is required for browser checks')
    windows = browser.lower().endswith('.exe')
    with tempfile.TemporaryDirectory(prefix='.browser-test-', dir=Path(__file__).parent) as temp:
        root = Path(temp).resolve()
        patch = ('diff --git a/example.R b/example.R\n'
                 '--- a/example.R\n+++ b/example.R\n'
                 '@@ -1,2 +1,2 @@\n-😀 estimate <- mean(x)\n+😀 estimate <- mean(x, na.rm = TRUE)\n context\n'
                 'diff --git a/README.md b/README.md\n'
                 'new file mode 100644\n--- /dev/null\n+++ b/README.md\n'
                 '@@ -0,0 +1,2 @@\n+<img src=x onerror="globalThis.pwned=true">\n+' + 'wide_' * 100 + '\n')
        files = dr.parse_patch(patch)
        dr.add_whole(files[0], '😀 estimate <- mean(x, na.rm = TRUE)\ncontext\nunchanged\n'.encode(), 'new')
        comments = [dict(id='c1', author='Codex', path='example.R', side='old',
                         line=1, body='Check deletion', matched=True),
                    dict(id='c2', author='Codex', path='README.md', side='new',
                         line=1, body='</script><script>globalThis.pwned=true</script>', matched=True),
                    dict(id='c3', author='Codex', path='unknown', side='new',
                         line=99, body='Unmatched note', matched=False)]
        page = root / 'review.html'
        html = dr.render(dict(comparison='Browser acceptance fixture', files=files,
                              comments=comments, context=3))
        # The harness is test-only; the generated tool has no test or persistence API.
        page.write_text(html.replace('</body>', '<script>' + CHECKS.replace('</script', r'<\/script') + '</script></body>'))
        def browser_path(path):
            if windows:
                return subprocess.check_output(['wslpath', '-w', str(path)], text=True).strip()
            return str(path)
        uri = ('file:///' + browser_path(page).replace('\\', '/')) if windows else page.as_uri()
        result = subprocess.run([browser, '--headless', '--disable-gpu', '--no-first-run',
                                 '--no-sandbox', '--disable-background-networking',
                                 '--user-data-dir=' + browser_path(root / 'profile'),
                                 '--dump-dom', '--virtual-time-budget=5000', uri],
                                capture_output=True, text=True, timeout=40)
        match = re.search(r'<pre id="testResults">(.*?)</pre>', result.stdout, re.S)
        if not match:
            raise AssertionError('Browser did not complete checks: ' + result.stderr[-1500:])
        outcome = json.loads(match[1])
        assert outcome.get('passed') and outcome.get('reload'), outcome
        assert sorted(p.name for p in root.iterdir()) == ['profile', 'review.html']
    print('Browser checks passed: file://, comments, filters, whole-file toggle, clipboard API/fallback, manual copy, reload and escaping.')


if __name__ == '__main__':
    main()
