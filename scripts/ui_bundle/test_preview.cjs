/* Repository-only browser regression. No server or app/VM is launched.
 * npm install --prefix .cache/ui-preview-test --no-audit --no-fund puppeteer-core@24
 * node scripts/ui_bundle/test_preview.cjs <extracted directory> <report directory>
 * Requires pinned chrome-headless-shell 148.0.7778.97 in the test cache (not shipped).
 * Use a bounded process-tree cgroup and disposable profile; the preview's CSP
 * blocks provider/network connections.
 */
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../..');
const puppeteer = require(path.join(root, '.cache/ui-preview-test/node_modules/puppeteer-core'));
const input = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
fs.mkdirSync(output, {recursive:true});
const results = [];
(async () => {
  const browser = await puppeteer.launch({browser:'chrome',executablePath:path.join(root,'.cache/ui-preview-test/browsers/chrome-headless-shell/linux-148.0.7778.97/chrome-headless-shell-linux64/chrome-headless-shell'),headless:true,
    args:['--disable-gpu'],defaultViewport:{width:1568,height:832},timeout:30000});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    const url = pathToFileURL(path.join(input,'index.html')).href;
    const check = (condition, name) => { assert(condition,name);results.push(name); };
    const open = async screen => { await page.goto(url+'?screen='+encodeURIComponent(screen),{waitUntil:'load',timeout:20000});await page.evaluate(()=>document.fonts.ready); };
    await open('workspace');
    check(await page.evaluate(()=>!!window.NarwhalPreview),'preview initialized');
    check(await page.$$eval('.categories > button',nodes=>nodes.length)===15,'all 15 categories');
    check(await page.$$eval('.menubar > button',nodes=>nodes.length)===16,'brand plus 15 menus');
    check(await page.$$eval('.rail button',nodes=>nodes.length)===5,'all five right panels');
    check(await page.evaluate(()=>document.fonts.check('12px Inter')),'bundled Inter font loaded');
    const rects=await page.evaluate(()=>{const r=s=>document.querySelector(s).getBoundingClientRect().toJSON();return {scale:NarwhalPreview.state.scale,left:r('.left'),rail:r('.rail'),menu:r('.menubar'),category:r('.categories'),console:r('.console'),input:r('.ai-input'),status:r('.status')};});
    for(const [key,size,dimension] of [['left',126,'width'],['rail',72,'width'],['menu',26,'height'],['category',78,'height'],['console',75,'height'],['input',72,'height'],['status',37,'height']])check(Math.abs(rects[key][dimension]-size*rects.scale)<1.5,'source logical size '+key);
    check(await page.evaluate(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0)),'all visible artwork loaded');
    await page.screenshot({path:path.join(output,'workspace-light.png')});
    await page.click('.title-label');check(await page.$$eval('.popup-menu',n=>n.length)===0,'title text activates without menu');
    await page.click('.title-label',{clickCount:2});check(await page.$eval('.viewports',n=>n.classList.contains('max')),'title double-click maximizes');
    await page.click('.title-label',{clickCount:2});check(!await page.$eval('.viewports',n=>n.classList.contains('max')),'title double-click restores');
    await page.click('.title-arrow');check(await page.$$eval('.popup-menu',n=>n.length)>0,'title arrow opens real menu');await page.keyboard.press('Escape');
    await page.click('.vp canvas',{button:'right'});check(await page.$$eval('.popup-menu .item',n=>n.length)>5,'source viewport context menu');await page.keyboard.press('Escape');
    await page.click('.status [data-pane="gumball"]',{button:'right'});check(await page.$$eval('.popup-menu .item',n=>n.length)>8,'complete source Gumball menu');await page.keyboard.press('Escape');
    await page.click('#vptabs button',{button:'right'});check(await page.$$eval('.popup-menu .item',n=>n.length)>2,'source viewport-tab menu');await page.keyboard.press('Escape');
    await page.click('[data-category="Transform"]');check(await page.$eval('#left-title',n=>n.textContent)==='Transform','category changes palette');
    await page.click('#tools .tool');check(await page.evaluate(()=>NarwhalPreview.state.command)==='Move','actual tool command payload selects Move');await page.keyboard.press('Escape');
    await page.click('[data-category="Standard"]');await page.click('#tools .flyout-arrow');
    check(await page.$$eval('.flyout-grid .tool',n=>n.length)>1,'actual flyout opens');
    check(Math.abs(await page.$eval('.flyout-grid .tool',n=>n.getBoundingClientRect().width)-32*rects.scale)<1,'source 32px flyout cells');await page.keyboard.press('Escape');
    await page.evaluate(()=>[...document.querySelectorAll('#menubar>button')].find(n=>n.textContent==='File').click());
    check(await page.$$eval('.popup-menu .item',n=>n.length)>10,'full File menu');
    await page.evaluate(()=>[...document.querySelectorAll('.popup-menu .item')].find(n=>/^Open/.test(n.dataset.label)).click());
    check(await page.evaluate(()=>NarwhalDialogs.snapshot()?.name)==='open','File Open routes to dialog without guessing label token');await page.keyboard.press('Escape');
    await page.evaluate(()=>[...document.querySelectorAll('#menubar>button')].find(n=>n.textContent==='View').click());
    await page.hover('.popup-menu .item[data-label="Theme"]');await page.click('.popup-menu .item[data-label="Dark"]');
    check(await page.evaluate(()=>NarwhalPreview.state.theme)==='dark','nested View Theme changes actual palette');await page.screenshot({path:path.join(output,'workspace-dark.png')});await page.evaluate(()=>NarwhalPreview.applyTheme('light'));
    for(const tab of ['Layers','Properties','Display','Bug Report','AI Assistant']) {await page.click('.rail button[data-tab="'+tab+'"]');check(await page.$eval('.panel-title',n=>n.firstChild.textContent)===tab,'panel '+tab);}
    await page.click('#ai-input');await page.keyboard.type('Keep the chat focus');check(await page.$eval('#ai-input',n=>n.value)==='Keep the chat focus','typing stays in chat textarea');
    await page.evaluate(()=>[...document.querySelectorAll('.ai-buttons button')].find(n=>n.textContent==='Send').click());check(await page.$eval('.ai-transcript',n=>n.textContent.includes('No provider contacted')),'Send remains visibly non-network simulation');
        check(await page.$eval('.ai-transcript .status',n=>getComputedStyle(n).display==='block'),'chat status is not styled as the footer bar');
    await page.evaluate(()=>NarwhalPreview.openAISettings());await page.screenshot({path:path.join(output,'ai-settings.png')});
    check(await page.$$eval('.settings-card',n=>n.length)===3,'three actual AI settings cards');
    await page.$eval('input[aria-label="Model ID"]',n=>{n.value='discard-me';n.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.evaluate(()=>[...document.querySelectorAll('.settings-footer button')].find(n=>n.textContent==='Cancel').click());
    check(await page.evaluate(()=>NarwhalPreview.state.ai.config.model)!=='discard-me','settings Cancel preserves original draft');
    await page.evaluate(()=>NarwhalPreview.openAISettings());await page.$eval('input[aria-label="Model ID"]',n=>{n.value='preview-model';n.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.evaluate(()=>[...document.querySelectorAll('.settings-footer button')].find(n=>n.textContent==='Save settings').click());check(await page.evaluate(()=>NarwhalPreview.state.ai.config.model)==='preview-model','settings Save changes preview state');
    for(const tab of ['New','Recent','Open','About']) {await page.evaluate(tab=>NarwhalPreview.showWelcome(tab),tab);check(await page.$('.welcome-window'),'welcome '+tab);if(tab==='New')await page.screenshot({path:path.join(output,'welcome-new.png')});if(tab==='Recent')await page.screenshot({path:path.join(output,'welcome-recent.png')});await page.keyboard.press('Escape');}
    const catalog=await page.evaluate(()=>NarwhalDialogs.catalog());
    for(const item of catalog){await page.evaluate(name=>NarwhalPreview.openDialog(name,{candidates:[{key:'a',label:'Curve A'},{key:'b',label:'Face B'}],files:[{name:'Sample.3dm',path:'/preview/Sample.3dm'}]}),item.name);check(await page.evaluate(()=>!!NarwhalDialogs.snapshot()),'dialog '+item.name);if(['print','color','open'].includes(item.name))await page.screenshot({path:path.join(output,item.name+'.png')});await page.keyboard.press('Escape');check(await page.evaluate(()=>NarwhalDialogs.snapshot()===null),'Escape cancels '+item.name);}
    await page.evaluate(()=>NarwhalPreview.openPicker(400,260));await page.hover('.cad-dialog-menu-item');check(await page.evaluate(()=>!!NarwhalPreview.state.hoverCandidate),'picker hover supplies highlight identity');await page.keyboard.press('Escape');check(await page.evaluate(()=>!NarwhalPreview.state.hoverCandidate),'picker dismissal clears hover');
    await page.keyboard.press('F1');check(await page.$eval('#inventory',n=>n.open),'F1 opens separate interface navigator');check(await page.$$eval('.guide-grid button',n=>n.length)>=30,'navigator exposes interface states');await page.keyboard.press('Escape');
    await page.setViewport({width:1024,height:768});await page.evaluate(()=>NarwhalPreview.setTab('AI Assistant'));check(await page.$eval('.ai-input',n=>n.getBoundingClientRect().height>20),'narrow viewport retains composer');await page.screenshot({path:path.join(output,'workspace-narrow.png')});
    // Process-local mutation: remove the actual double-click handler, observe RED,
    // then reload source and verify restored behavior. No on-disk edits.
    await open('workspace');await page.$eval('.title-label',n=>n.ondblclick=null);await page.click('.title-label',{clickCount:2});let red=false;try{assert(await page.$eval('.viewports',n=>n.classList.contains('max')));}catch{red=true;}check(red,'MUTATION RED: missing double-click handler detected');await open('workspace');await page.click('.title-label',{clickCount:2});check(await page.$eval('.viewports',n=>n.classList.contains('max')),'restored double-click GREEN');
    await open('workspace');await page.$eval('.ai-input',n=>n.addEventListener('keydown',()=>document.getElementById('command').focus()));await page.click('#ai-input');await page.keyboard.type('x');red=false;try{assert(await page.$eval('#ai-input',n=>document.activeElement===n));}catch{red=true;}check(red,'MUTATION RED: chat focus theft detected');await open('workspace');await page.click('#ai-input');await page.keyboard.type('x');check(await page.$eval('#ai-input',n=>document.activeElement===n),'restored chat focus GREEN');
    check(errors.length===0,'no browser runtime errors: '+errors.slice(0,3).join('; '));
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed:results.length,checks:results,rects,errors},null,2));
    console.log('Browser preview: '+results.length+' checks passed; screenshots/report: '+output);
  } finally {await browser.close();}
})().catch(error=>{console.error(String(error));process.exitCode=1;});
