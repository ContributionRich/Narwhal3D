/* UI-only browser port. Source: design.py, ui.py, shell_popup.py, rightpanel.py,
 * ai_assistant.panel_layout and ai_settings_popup.PopupModel. Scene, files and
 * chat are isolated design fixtures. No provider, CAD or application preferences. */
(() => {
  'use strict';
  const D = window.NARWHAL_UI;
  if (!D) { document.body.textContent = 'Missing data/ui_data.js. Extract the whole ZIP before opening index.html.'; return; }
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const button = (text, callback, cls = '') => { const n = el('button', cls, text); n.type = 'button'; if (callback) n.onclick = callback; return n; };
  const image = (name, alt = '') => { const n = el('img'); n.src = 'assets/icons/' + name; n.alt = alt; return n; };
  const params = new URLSearchParams(location.search);
  const S = {
    theme: params.get('theme') === 'dark' ? 'dark' : D.default_theme, scale: 1, scaleMode: params.get('scale') || 'auto',
    category: D.categories[0].name, activeVp: 'Top', maximized: false, tab: 'AI Assistant', dockWidth: 280,
    history: ['SelNone: 0 objects selected'], command: null, commandStep: 0, showUnavailable: true,
    layers: [{name:'Default',color:'#000000',on:true,locked:false}], currentLayer:0, layerQuery:'',
    osnap: new Set(D.osnap_default_on), flags:{osnap:true,gumball:true,grid_snap:false,ortho:false,record_history:false},
    coords:['0.000','0.000','0.000'], units:'Millimeters', tolerance:'0.01', sample:false, selected:false, hoverCandidate:null,
    viewModes:{Top:'Wireframe',Perspective:'Wireframe',Front:'Wireframe',Right:'Wireframe'}, displayScope:'Active',
    ai:{messages:[],input:'',status:'disconnected',includeSelection:true,config:{...D.panels.ai.defaults}},
    bugText:'', bugCategory:D.panels.bug.categories[0]?.[0] || 'Other', draft:null
  };
  const popups = [];
  let menuOwner = null, tipTimer = null;
  function applyTheme(name) {
    S.theme = D.themes[name] ? name : D.default_theme;
    for (const [key, value] of Object.entries(D.themes[S.theme])) document.documentElement.style.setProperty('--' + key, value);
    for (const [key, value] of Object.entries(D.panels.ai_settings.palettes[S.theme])) document.documentElement.style.setProperty('--ai-' + key, value);
    document.documentElement.dataset.theme = S.theme;
    document.documentElement.style.colorScheme = S.theme;
    window.NarwhalWelcome?.setTheme(S.theme); window.NarwhalDialogs?.setTheme(S.theme);
    paintAll();
  }
  function sizeWorkspace() {
    const value = Number(S.scaleMode);
    S.scale = S.scaleMode === 'auto' ? Math.min(1, innerWidth / 2020) : Math.max(.5, Math.min(2, value || 1));
    const style = document.documentElement.style;
    style.setProperty('--preview-scale', S.scale);
    style.setProperty('--logical-width', innerWidth / S.scale + 'px');
    style.setProperty('--logical-height', innerHeight / S.scale + 'px');
    style.setProperty('--dock-width', S.dockWidth + 'px');
    closeMenus(); hideTip();
    if (!$('ai-settings').hidden) positionAISettings();
    requestAnimationFrame(paintAll);
  }
  function log(text) { S.history.push(text); S.history = S.history.slice(-100); renderHistory(); }
  function renderHistory() { $('history').replaceChildren(...S.history.slice(-1).map(t => el('div', '', t))); }
  function setPrompt(text, options = []) {
    const n = $('prompt'); n.replaceChildren(document.createTextNode(text));
    for (const opt of options) n.append(button(opt, () => log(opt + ': interface preview only'), 'opt'));
  }
  function notifyPreview(name) { log(name + ': interface preview — no CAD operation executed.'); }
  function runCommand(name) {
    const command = String(name || '').trim().replace(/^_/, '').toLowerCase();
    const dialogs = {open:'open',save:'save',saveas:'save',print:'print',options:'preferences',preferences:'preferences',units:'units'};
    if (dialogs[command]) { openDialog(dialogs[command]); return; }
    if (command === 'new') { showWelcome('New'); return; }
    if (command === 'selnone') { S.selected = false; log('SelNone: 0 objects selected'); renderPanel(); paintAll(); return; }
    if (command === 'selall') { S.selected = S.sample; log(S.sample ? '1 object selected (preview fixture)' : '0 objects selected'); renderPanel(); paintAll(); return; }
    if (command === 'escape' || command === 'cancel') { cancelCommand(); return; }
    const real = D.commands.find(c => c.toLowerCase() === command);
    if (!real) { log((name || 'Command') + ' — not implemented yet'); return; }
    const descriptor = D.command_catalog?.find(c => c.name === real);
    S.command = real; S.commandStep = 0; log('Command: ' + real);
    const step = descriptor?.steps?.[0];
    setPrompt(step ? step[0] : real + ':', descriptor?.options?.slice(0, 10) || []);
    $('command').focus();
  }
  function cancelCommand() { S.command = null; S.commandStep = 0; $('command').value = ''; $('completions').hidden = true; setPrompt('Command:'); }
  function wireConsole() {
    const input = $('command'), list = $('completions'); let matches = [], selected = 0;
    input.oninput = () => {
      const q = input.value.toLowerCase(); matches = !q || S.command ? [] : D.commands.filter(c => c.toLowerCase().startsWith(q)).slice(0,10); selected = 0;
      list.replaceChildren(...matches.map((name,i) => { const li = el('li',i===0?'sel':'',name); li.onmousedown = e => { e.preventDefault(); input.value=''; list.hidden=true; runCommand(name); }; return li; })); list.hidden = !matches.length;
    };
    input.onkeydown = e => {
      if (e.key === 'Escape') { e.stopPropagation(); cancelCommand(); return; }
      if (['ArrowDown','ArrowUp'].includes(e.key) && !list.hidden) { e.preventDefault(); selected=(selected+(e.key==='ArrowDown'?1:matches.length-1))%matches.length; [...list.children].forEach((li,i)=>li.classList.toggle('sel',i===selected)); return; }
      if (e.key !== 'Enter' && !(e.key === 'Tab' && !list.hidden)) return;
      e.preventDefault(); const value = !list.hidden ? matches[selected] : input.value.trim(); input.value=''; list.hidden=true;
      if (S.command) {
        const descriptor=D.command_catalog?.find(c=>c.name===S.command); const next=descriptor?.steps?.[++S.commandStep];
        if(next) setPrompt(next[0],descriptor.options?.slice(0,10)); else { notifyPreview(S.command); cancelCommand(); }
      } else if(value) runCommand(value);
    };
    document.addEventListener('keydown', e => {
      if(e.key==='F1') { e.preventDefault(); openInventory(); return; }
      if(document.activeElement?.matches('input,textarea,select,[contenteditable=true]')) return;
      if(document.querySelector('.welcome-window') || window.NarwhalDialogs?.snapshot() || !$('ai-settings').hidden || $('inventory').open || popups.length) return;
      if(e.key==='Escape') { cancelCommand(); return; }
      if(e.key.length===1 && !e.ctrlKey && !e.metaKey && !e.altKey) input.focus();
    });
  }
  function hideTip() { clearTimeout(tipTimer); $('tooltip').hidden=true; }
  function showTip(text,x,y) { hideTip(); tipTimer=setTimeout(()=>{const t=$('tooltip');t.textContent=text;t.hidden=false;t.style.left=Math.min(x+12,innerWidth-t.offsetWidth*S.scale-5)+'px';t.style.top=Math.min(y+16,innerHeight-t.offsetHeight*S.scale-5)+'px';},350); }
  function closeMenus(from=0) { while(popups.length>from) popups.pop().remove(); if(!from){menuOwner?.classList.remove('open');menuOwner=null;} }
  function openMenu(rows,x,y,owner=null,depth=0,title='') {
    closeMenus(depth); hideTip();
    const menu=el('div','popup-menu'); menu.setAttribute('role','menu'); menu.dataset.depth=depth;
    if(title) menu.append(el('h3','',title));
    let visible=rows.filter(row=>row.separator || S.showUnavailable || row.enabled!==false);
    visible=visible.filter((row,i)=>!row.separator || (i>0 && i<visible.length-1 && !visible[i-1].separator));
    for(const row of visible){
      if(row.separator){menu.append(el('div','sep'));continue;}
      const item=button('',null,'item'+(row.enabled===false?' disabled':''));item.setAttribute('role','menuitem'); item.dataset.label=row.label;item.setAttribute('aria-disabled',row.enabled===false?'true':'false');
      if(row.checked)item.append(el('span','check','✓'));
      item.append(el('span','row-label',row.label));if(row.shortcut)item.append(el('span','shortcut',row.shortcut));if(row.children)item.append(el('span','','›'));
      const sub=()=>{if(!row.children)return;const r=item.getBoundingClientRect();openMenu(row.children,r.right+2,r.top,null,depth+1);};
      item.onmouseenter=()=>{closeMenus(depth+1);if(row.children)sub();};
      item.onclick=()=>{if(row.children){sub();return;}closeMenus();if(row.enabled===false){log(row.label+' — not implemented yet');return;}if(typeof row.action==='function')row.action();else dispatchMenu(row);};
      item.onkeydown=e=>{const items=[...menu.querySelectorAll(':scope > .item')];const i=items.indexOf(item);if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();items[(i+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus();}else if(e.key==='ArrowRight'&&row.children){e.preventDefault();sub();popups[depth+1]?.querySelector('.item')?.focus();}else if(e.key==='ArrowLeft'&&depth){e.preventDefault();closeMenus(depth);popups[depth-1]?.querySelector('.item')?.focus();}else if(e.key==='Escape'){e.preventDefault();closeMenus();}};
      menu.append(item);
    }
    $('popup-root').append(menu);popups.push(menu);
    const width=menu.offsetWidth*S.scale,height=menu.offsetHeight*S.scale;
    menu.style.left=Math.max(2,Math.min(x,innerWidth-width-3))+'px';menu.style.top=Math.max(2,Math.min(y,innerHeight-height-3))+'px';
    if(owner){menuOwner=owner;owner.classList.add('open');}
    return menu;
  }
  function dispatchMenu(row){
    if(row.previewAction==='theme'){applyTheme(row.value);return;}
    if(row.previewAction==='unavailable'){S.showUnavailable=!S.showUnavailable;renderTools();return;}
    if(row.previewAction==='welcome'){showWelcome('Recent');return;}
    if(row.command){runCommand(row.command);return;}
    notifyPreview(row.label);
  }
  function renderMenubar(){
    const bar=$('menubar');bar.replaceChildren();const logo=button('',()=>showWelcome('Recent'),'brand');logo.setAttribute('aria-label','Welcome');const img=el('img');img.src='assets/branding/narwhal3d.png';img.alt='Narwhal';logo.append(img);bar.append(logo);
    for(const m of D.menus){const b=button(m.label,()=>{if(menuOwner===b){closeMenus();return;}const r=b.getBoundingClientRect();let rows=m.rows;
      if(m.label==='View')rows=[...rows,{separator:true},{label:'Theme',children:['Light','Dark'].map(n=>({label:n,checked:S.theme===n.toLowerCase(),action:()=>applyTheme(n.toLowerCase())}))},{label:'Show Unavailable',checked:!S.showUnavailable,action:()=>{S.showUnavailable=!S.showUnavailable;renderTools();}}];
      openMenu(rows,r.left,r.bottom,b);});b.onmouseenter=()=>{if(menuOwner&&menuOwner.parentNode===bar&&menuOwner!==b)b.click();};bar.append(b);}
  }
  function renderCategories(){const nav=$('categories');nav.replaceChildren();for(const c of D.categories){const b=button('',()=>setCategory(c.name),c.name===S.category?'active':'');b.dataset.category=c.name;b.append(image(c.icon),el('span','',c.name));nav.append(b);}}
  function setCategory(name){if(!D.categories.some(c=>c.name===name))return;S.category=name;renderCategories();renderTools();}
  function toolButton(t,inFlyout=false){
    const b=button('',null,'tool'+(t.enabled?'':' unavailable'));b.setAttribute('aria-label',t.text||t.tooltip||'Tool');if(t.icon)b.append(image(t.icon));
    b.onmouseenter=e=>showTip((t.tooltip||t.text)+(t.enabled?'':' - not implemented yet')+(t.right_tooltip?'\nright-click: '+t.right_tooltip:''),e.clientX,e.clientY);b.onmouseleave=hideTip;
    b.onclick=()=>{hideTip();if(inFlyout)closeMenus();if(t.enabled&&t.command)runCommand(t.command);else log((t.tooltip||t.text)+' — not implemented yet');};
    b.oncontextmenu=e=>{e.preventDefault();hideTip();if(t.right_command&&t.right_enabled)runCommand(t.right_command);else if(t.right_tooltip)notifyPreview(t.right_tooltip);};
    if(t.flyout){const arrow=el('span','flyout-arrow');arrow.setAttribute('role','button');arrow.setAttribute('aria-label','Open '+t.flyout+' flyout');arrow.onclick=e=>{e.stopPropagation();openFlyout(t,b.getBoundingClientRect());};b.append(arrow);}return b;
  }
  function openFlyout(t,r){closeMenus();const menu=el('div','popup-menu');menu.append(el('h3','',t.flyout));const grid=el('div','flyout-grid');for(const item of D.flyouts[t.flyout]||[])if(!item.separator&&(S.showUnavailable||item.enabled))grid.append(toolButton(item,true));menu.append(grid);$('popup-root').append(menu);popups.push(menu);menu.style.left=Math.min(r.right+2,innerWidth-menu.offsetWidth*S.scale-4)+'px';menu.style.top=Math.min(r.top,innerHeight-menu.offsetHeight*S.scale-4)+'px';}
  function renderTools(){$('left-title').textContent=S.category;const tools=D.categories.find(c=>c.name===S.category).tools;$('tools').replaceChildren(...tools.filter(t=>!t.separator&&(S.showUnavailable||t.enabled)).map(t=>toolButton(t)));}
  $('left-header').onclick=e=>{const r=e.currentTarget.getBoundingClientRect();openMenu(D.categories.map(c=>({label:c.name,checked:c.name===S.category,action:()=>setCategory(c.name)})),r.left,r.bottom,e.currentTarget);};
  function renderOsnap(){const ul=$('osnap');ul.replaceChildren();for(const row of D.osnap_rows){const on=row.label==='Disable'?!S.flags.osnap:S.osnap.has(row.label);const li=el('li',on?'on':'');li.setAttribute('role','checkbox');li.setAttribute('aria-checked',String(on));li.tabIndex=0;li.append(el('span','box',on?'✓':''),el('span','',row.label+(row.computed?'':' *')));li.onclick=()=>{if(row.label==='Disable')S.flags.osnap=!S.flags.osnap;else if(on)S.osnap.delete(row.label);else S.osnap.add(row.label);renderOsnap();renderStatus();};li.onkeydown=e=>{if(e.key===' '){e.preventDefault();li.click();}};ul.append(li);}}
  function setActive(name){S.activeVp=name;for(const vp of $('viewports').children)vp.classList.toggle('active',vp.dataset.view===name);renderViewportTabs();if(S.tab==='Display')renderPanel();paintAll();}
  function titleRows(name){return D.viewport_title_menu.flatMap(row=>row.display_modes?row.display_modes.map(m=>({label:m.label,enabled:m.enabled,shortcut:m.shortcut,checked:S.viewModes[name]===m.label,action:()=>{S.viewModes[name]=m.label;paintAll();if(S.tab==='Display')renderPanel();}})):row.children?[{...row,children:convertTitleChildren(row.children,name)}]:[{...row,action:()=>{if(/maximize/i.test(row.label)){S.maximized=!S.maximized;$('viewports').classList.toggle('max',S.maximized);paintAll();}else notifyPreview(name+': '+row.label);}}]);}
  function convertTitleChildren(rows,name){return rows.flatMap(row=>row.display_modes?row.display_modes.map(m=>({label:m.label,enabled:m.enabled,action:()=>{S.viewModes[name]=m.label;paintAll();}})):row.children?[{...row,children:convertTitleChildren(row.children,name)}]:[{...row,action:()=>notifyPreview(name+': '+row.label)}]);}
  function renderViewports(){const root=$('viewports');root.replaceChildren();for(const view of D.viewports){const vp=el('div','vp'+(S.activeVp===view.name?' active':''));vp.dataset.view=view.name;vp.append(el('canvas'));
    const title=el('div','vp-title');const label=button(view.name,e=>{e.stopPropagation();setActive(view.name);},'title-label');label.ondblclick=e=>{e.stopPropagation();setActive(view.name);S.maximized=!S.maximized;root.classList.toggle('max',S.maximized);requestAnimationFrame(paintAll);};const arrow=button('▾',e=>{e.stopPropagation();setActive(view.name);const r=title.getBoundingClientRect();openMenu(titleRows(view.name),r.left,r.bottom+2,arrow);},'title-arrow');arrow.setAttribute('aria-label',view.name+' viewport menu');title.append(label,arrow);vp.append(title);
    const corner=button('⛶',e=>{e.stopPropagation();setActive(view.name);S.maximized=!S.maximized;root.classList.toggle('max',S.maximized);requestAnimationFrame(paintAll);},'vp-corner');corner.setAttribute('aria-label','Maximize '+view.name);vp.append(corner);
    vp.oncontextmenu=e=>{e.preventDefault();const rows=D.context_menus?.[S.selected?'Object':'Viewport']||[];openMenu(rows.map(row=>({...row,action:()=>row.command?runCommand(row.command):notifyPreview(row.label)})),e.clientX,e.clientY);};
        vp.onpointerdown=e=>{if(e.button!==0||e.target.closest('button'))return;setActive(view.name);if(S.sample){if(e.shiftKey||e.ctrlKey){openPicker(e.clientX,e.clientY);return;}S.selected=!S.selected;renderPanel();paintAll();}};
    vp.onpointermove=e=>{if(e.target.tagName!=='CANVAS')return;const r=vp.getBoundingClientRect();S.coords=[((e.clientX-r.left-r.width/2)/(20*S.scale)).toFixed(3),((r.height/2-e.clientY+r.top)/(20*S.scale)).toFixed(3),'0.000'];const slot=document.querySelector('.coordinates');if(slot)slot.textContent='x '+S.coords[0]+'  y '+S.coords[1]+'  z '+S.coords[2];};root.append(vp);
  }renderViewportTabs();requestAnimationFrame(paintAll);}
  function renderViewportTabs(){$('vptabs').replaceChildren(...['Perspective','Top','Front','Right'].map(name=>{const b=button(name,()=>setActive(name),name===S.activeVp?'active':'');b.oncontextmenu=e=>{e.preventDefault();openMenu(D.viewport_tab_menu||[],e.clientX,e.clientY);};return b;}),button('+',e=>{const r=e.currentTarget.getBoundingClientRect();openMenu(D.viewport_tab_add_menu||[],r.left,r.top-100);}));}
  function paintAll(){for(const vp of document.querySelectorAll('.vp'))paintViewport(vp);}
  function paintViewport(vp){
    const canvas=vp.querySelector('canvas');if(!canvas||!vp.clientWidth||!vp.clientHeight)return;const w=vp.clientWidth,h=vp.clientHeight,dpr=Math.min(2,devicePixelRatio||1);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const c=canvas.getContext('2d');c.scale(dpr,dpr);const t=D.themes[S.theme];c.fillStyle=t.VIEWPORT_BG;c.fillRect(0,0,w,h);c.lineWidth=.7;
    const line=(x0,y0,x1,y1,color)=>{c.strokeStyle=color;c.beginPath();c.moveTo(x0,y0);c.lineTo(x1,y1);c.stroke();};
    if(vp.dataset.view==='Perspective'){
      // Display-only planar grid; never claimed as CAD projection/model geometry.
      const project=(x,y)=>{const z=1+(x+y)*.016;return [w*.51+(x-y)*18/z,h*.48+(x+y)*7/z];};
      for(let i=-22;i<=30;i++){const color=i%5===0?t.GRID_MAJOR:t.GRID_MINOR;const a=project(i,-12),b=project(i,30),d=project(-12,i),e=project(30,i);line(...a,...b,color);line(...d,...e,color);}line(...project(0,0),...project(30,0),t.AXIS_X);line(...project(0,0),...project(0,30),t.AXIS_Y);
    }else{const dx=18,ox=w/2,oy=h/2;for(let i=Math.floor(-ox/dx);i<Math.ceil(ox/dx);i++)line(ox+i*dx,0,ox+i*dx,h,i%5===0?t.GRID_MAJOR:t.GRID_MINOR);for(let i=Math.floor(-oy/dx);i<Math.ceil(oy/dx);i++)line(0,oy+i*dx,w,oy+i*dx,i%5===0?t.GRID_MAJOR:t.GRID_MINOR);line(ox,oy,w,oy,t.AXIS_X);line(ox,oy,ox,0,t.AXIS_Y);}
    if(S.sample){c.strokeStyle=S.hoverCandidate?'#d94584':S.selected?'#ffda00':t.WIRE;c.lineWidth=1.6;c.strokeRect(w*.32,h*.33,w*.28,h*.30);c.beginPath();c.moveTo(w*.25,h*.58);c.bezierCurveTo(w*.4,h*.2,w*.55,h*.8,w*.7,h*.35);c.stroke();if(S.selected&&S.flags.gumball){line(w*.46,h*.48,w*.63,h*.48,'#da3838');line(w*.46,h*.48,w*.46,h*.25,'#32944e');c.fillStyle='#e24545';c.fillRect(w*.63-3,h*.48-3,6,6);}}
  }
  function renderRail(){$('rail').replaceChildren(...D.inspector_tabs.map(t=>{const b=button('',()=>setTab(t.name),t.name===S.tab?'active':'');b.dataset.tab=t.name;b.append(image(t.icon),el('span','',t.name));return b;}));}
  function setTab(name){if(!D.inspector_tabs.some(t=>t.name===name))return;S.tab=name;renderRail();renderPanel();}
  function renderPanel(){const panel=$('panel');panel.replaceChildren();const title=el('div','panel-title',S.tab);title.append(el('span','grow'));panel.append(title);const body=el('div','panel-body');panel.append(body);
    if(S.tab==='AI Assistant')renderAI(body);else if(S.tab==='Layers')renderLayers(body,title);else if(S.tab==='Properties')renderProperties(body);else if(S.tab==='Display')renderDisplay(body);else renderBug(body);
  }
  function renderAI(parent){const body=el('div','ai-body');parent.append(body);body.append(el('div','ai-heading','AI Assistant'));body.append(el('div','ai-connection',S.ai.config.model?'Connection: Configured, not connected':'Connection: Not connected'));const context=el('label','ai-context');const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=S.ai.includeSelection;checkbox.onchange=()=>S.ai.includeSelection=checkbox.checked;context.append(checkbox,document.createTextNode('Include selection context ('+(S.selected?1:0)+')'));body.append(context);const transcript=el('div','ai-transcript');if(!S.ai.messages.length)transcript.append(el('div','placeholder','Ask about selected objects, CAD workflows, or future actions.'));for(const m of S.ai.messages)transcript.append(el('p','message '+m.role,({user:'You: ',assistant:'Assistant: ',status:'Status: '}[m.role]||'')+m.text));body.append(transcript);const input=el('textarea','ai-input');input.id='ai-input';input.placeholder='Type a message...';input.value=S.ai.input;input.oninput=()=>S.ai.input=input.value;input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendAI();}};body.append(input);body.append(el('div','ai-status',S.ai.status==='preview'?'Preview only. No provider contacted. Tool actions are disabled.':'Open Settings to configure a provider. Tool actions are disabled.'));const actions=el('div','ai-buttons');actions.append(button('Send',sendAI),button('New Chat',()=>{S.ai.messages=[];S.ai.input='';S.ai.status='disconnected';renderPanel();}),button('Settings',openAISettings));body.append(actions);}
  function sendAI(){if(!S.ai.input.trim())return;S.ai.messages.push({role:'user',text:S.ai.input.trim()},{role:'status',text:'Interface preview only. No provider contacted; no CAD operation executed.'});S.ai.input='';S.ai.status='preview';renderPanel();}
  function layerMenu(index,anchor){const r=anchor.getBoundingClientRect();openMenu((D.layer_menu||[]).map(row=>row?{...row,action:()=>{if(/rename/i.test(row.label))openDialog('picker',{title:'Layer name',candidates:[]});else if(/delete/i.test(row.label)&&index>0){S.layers.splice(index,1);S.currentLayer=0;renderPanel();}else notifyPreview(row.label);}}:{separator:true}),r.left,r.bottom);}
  function renderLayers(body,title){title.append(button('+',()=>{S.layers.push({name:'Layer '+String(S.layers.length).padStart(2,'0'),color:'#808080',on:true,locked:false});renderPanel();}),button('⋯',e=>layerMenu(S.currentLayer,e.currentTarget)));const search=el('label','search');search.append(el('span','','⌕'));const input=el('input');input.placeholder='Search layers...';input.value=S.layerQuery;search.append(input);body.append(search);const list=el('div','layers');body.append(list);
    const rows=()=>{list.replaceChildren();const head=el('div','layer-row head');['Name','✓','■','◉','♙','⋯'].forEach(t=>head.append(el('span','',t)));list.append(head);S.layers.forEach((layer,i)=>{if(!layer.name.toLowerCase().includes(S.layerQuery.toLowerCase()))return;const row=el('div','layer-row'+(i===S.currentLayer?' current':''));row.append(el('span','',layer.name),button(i===S.currentLayer?'✓':'',()=>{S.currentLayer=i;rows();renderStatus();}));const swatch=button('',()=>openDialog('color',{color:layer.color,onColor:value=>{layer.color=Array.isArray(value)?'#'+value.map(v=>Math.round(v).toString(16).padStart(2,'0')).join(''):value;rows();}}),'swatch');swatch.style.background=layer.color;row.append(swatch,button(layer.on?'◉':'○',()=>{layer.on=!layer.on;rows();}),button(layer.locked?'▣':'♙',()=>{layer.locked=!layer.locked;rows();}),button('⋯',e=>layerMenu(i,e.currentTarget)));row.ondblclick=()=>{S.currentLayer=i;rows();renderStatus();};list.append(row);});};input.oninput=()=>{S.layerQuery=input.value;rows();};rows();}
  function renderProperties(parent){if(!S.selected){parent.append(el('div','panel-note','No object selected'));return;}for(const [label,value] of D.panels.properties.sample){const row=el('div','property-row');row.append(el('span','',label));const input=el('input');input.value=String(value);input.setAttribute('aria-label',label);row.append(input);parent.append(row);}}
  function renderDisplay(parent){const panel=el('div','display-panel');parent.append(panel);panel.append(el('div','display-label','Apply to'));const scope=el('div','display-buttons');for(const name of ['Active','All'])scope.append(button(name,()=>{S.displayScope=name;renderPanel();},S.displayScope===name?'active':''));panel.append(scope,el('div','display-label','Display mode'));const modes=el('div','display-buttons');for(const mode of D.display_modes.filter(m=>m.implemented))modes.append(button(mode.name,()=>{for(const name of Object.keys(S.viewModes))if(S.displayScope==='All'||name===S.activeVp)S.viewModes[name]=mode.name;renderPanel();paintAll();},S.viewModes[S.activeVp]===mode.name?'active':''));panel.append(modes,el('div','display-label','Viewports'));for(const name of Object.keys(S.viewModes))panel.append(button(name+' — '+S.viewModes[name],()=>setActive(name),'display-view'+(S.activeVp===name?' active':'')));}
  function renderBug(parent){const panel=el('div','bug-panel');parent.append(panel);panel.append(el('div','','Bug Report'));const select=el('select');for(const entry of D.panels.bug.categories){const [value,label]=Array.isArray(entry)?entry:[entry,entry];const option=el('option','',label);option.value=value;select.append(option);}select.value=S.bugCategory;select.onchange=()=>S.bugCategory=select.value;panel.append(select);const text=el('textarea');text.placeholder='Describe the problem...';text.value=S.bugText;text.oninput=()=>S.bugText=text.value;panel.append(text);for(const entry of D.panels.bug.checks){const label=el('label');const checkbox=el('input');checkbox.type='checkbox';label.append(checkbox,document.createTextNode(Array.isArray(entry)?entry[1]:String(entry)));panel.append(label);}panel.append(el('small','','Reports and attachments stay disabled in this offline preview.'),button('Submit report',()=>notifyPreview('Bug report')));}
  function renderStatus(){const root=$('status');root.replaceChildren();for(const p of D.status_panes){let text=p.sample_text||p.label,cls='pane';if(p.id==='coordinates'){text='x '+S.coords[0]+'  y '+S.coords[1]+'  z '+S.coords[2];cls+=' readout coordinates';}else if(p.id==='units'){text=S.units;cls+=' readout units';}else if(p.id==='tolerance'){text='Absolute Tolerance: '+S.tolerance;cls+=' readout';}else if(p.id==='layer'){text=S.layers[S.currentLayer]?.name||'Default';cls+=' layer-pane';}if(S.flags[p.id])cls+=' on';if(!p.implemented)cls+=' todo';const b=button(text,e=>{const r=e.currentTarget.getBoundingClientRect();if(p.id==='units')openMenu(['Millimeters','Centimeters','Meters','Inches','Feet'].map(name=>({label:name,checked:S.units===name,action:()=>{S.units=name;renderStatus();}})),r.left,r.top-140);else if(p.id==='tolerance')openMenu(['0.1','0.01','0.001','0.0001'].map(value=>({label:value,checked:S.tolerance===value,action:()=>{S.tolerance=value;renderStatus();}})).concat([{label:'Custom…',action:()=>openDialog('tolerance',{values:{absolute_tolerance:S.tolerance,model_units:S.units},onSelect:value=>{S.tolerance=String(value.absolute_tolerance);renderStatus();}})}]),r.left,r.top-130);else if(p.id==='layer')openMenu(S.layers.map((l,i)=>({label:l.name,checked:S.currentLayer===i,action:()=>{S.currentLayer=i;renderStatus();}})),r.left,r.top-80);else if(!p.implemented)log(p.label+' — not implemented yet');else if(p.kind==='toggle'||p.id in S.flags){S.flags[p.id]=!S.flags[p.id];renderStatus();renderOsnap();paintAll();}else notifyPreview(p.label);},cls);b.dataset.pane=p.id;b.oncontextmenu=e=>{e.preventDefault();if(p.id==='gumball'){const r=b.getBoundingClientRect();openGumballMenu(r.left,r.top);}};root.append(b);}}
  function positionAISettings(){const panel=$('ai-settings');const dock=document.querySelector('.inspector').getBoundingClientRect();const width=Math.min(520,(innerWidth-16)/S.scale),height=Math.min(720,(innerHeight-16)/S.scale);panel.style.width=width+'px';panel.style.height=height+'px';panel.style.left=Math.max(8,dock.left-width*S.scale-8*S.scale)+'px';panel.style.top=Math.max(8,Math.min(dock.top,innerHeight-height*S.scale-8))+'px';}
  function openAISettings(){closeMenus();S.draft={...S.ai.config};renderAISettings();$('ai-settings').hidden=false;positionAISettings();}
  function renderAISettings(){const root=$('ai-settings');root.replaceChildren();const draft=S.draft;if(!draft)return;const head=el('header','settings-header');head.append(el('h2','','AI Assistant Settings'),el('p','','Connect Narwhal to your language model.'),button('×',closeAISettings,'settings-close'));root.append(head);const body=el('div','settings-body');root.append(body);
    const card=(title,subtitle,badge)=>{const n=el('section','settings-card'),h=el('div','card-head');h.append(el('span','card-badge',badge),el('h3','',title),el('p','',subtitle));n.append(h);body.append(n);return n;};
    const field=(parent,label,key,help='')=>{const wrap=el('label','settings-field');wrap.append(el('span','',label));const input=el('input');input.value=draft[key];input.oninput=()=>draft[key]=input.value;input.setAttribute('aria-label',label);wrap.append(input);if(help)wrap.append(el('small','',help));parent.append(wrap);return input;};
    const connection=card('Connection','Provider and API endpoint','↔');const provider=el('label','settings-field');provider.append(el('span','','Provider'));const select=el('select');select.setAttribute('aria-label','Provider');for(const [id,name] of D.panels.ai.providers){const o=el('option','',name);o.value=id;select.append(o);}select.value=draft.provider_id;select.onchange=()=>{const old=draft.provider_id;draft.provider_id=select.value;if(draft.endpoint===D.panels.ai.default_endpoints[old])draft.endpoint=D.panels.ai.default_endpoints[select.value];if(draft.credential_env===D.panels.ai.credential_env_names[old])draft.credential_env=D.panels.ai.credential_env_names[select.value];renderAISettings();};provider.append(select);connection.append(provider);field(connection,'API endpoint','endpoint','Local HTTP is supported. Use HTTPS for remote servers.');
    const models=card('Model','Discover models or enter an ID manually','≡');const row=el('div','refresh-row');const status=el('span','refresh-status','Refresh to discover models.');row.append(button('Refresh models',()=>{status.textContent='Mock discovery — no provider contacted.';},'settings-refresh'),status);models.append(row);const available=el('label','settings-field');available.append(el('span','','Available models'),button('No models discovered — enter an ID below',()=>{status.textContent='Mock discovery — no provider contacted.';},'settings-choice'));models.append(available);field(models,'Model ID','model','Manual entry is always available, even if discovery fails.');
    const security=card('Credentials & security','Authentication and connection security','♙');field(security,'API key environment variable','credential_env','Only the variable name is saved, never the secret key.');const check=el('label','security-check'),input=el('input');input.type='checkbox';input.checked=draft.allow_insecure_remote;const warning=el('div','security-warning','Security warning: remote HTTP can expose prompts and credentials. Use HTTPS whenever possible.');warning.hidden=!input.checked;input.onchange=()=>{draft.allow_insecure_remote=input.checked;warning.hidden=!input.checked;};check.append(input,document.createTextNode('Allow unencrypted remote HTTP'));security.append(check,warning);
    const footer=el('footer','settings-footer');footer.append(el('small','','Saved only on Save'),button('Cancel',closeAISettings),button('Save settings',()=>{S.ai.config={...draft};closeAISettings();renderPanel();}));root.append(footer);
  }
  function closeAISettings(){$('ai-settings').hidden=true;S.draft=null;}
  function openGumballMenu(x,y){const rows=[];for(const entry of D.gumball_menu||[]){rows.push({label:entry.label,enabled:entry.enabled,action:()=>{if(entry.key==='on'||entry.key==='off'){S.flags.gumball=entry.key==='on';renderStatus();paintAll();}else notifyPreview(entry.label);}});if(['reset','align_view','smooth','drag_strength'].includes(entry.key))rows.push({separator:true});}openMenu(rows,x,y-rows.length*22*S.scale);}
    function openDialog(name,options={}){closeMenus();hideTip();const defaults={};if(name==='preferences'){defaults.values={theme:S.theme,show_unavailable:S.showUnavailable};defaults.onSelect=value=>{applyTheme(value.theme);S.showUnavailable=value.show_unavailable;renderTools();};}if(name==='units'){defaults.values={model_units:S.units,absolute_tolerance:Number(S.tolerance),scale_model:true};defaults.onSelect=value=>{S.units=value.model_units;S.tolerance=String(value.absolute_tolerance);renderStatus();};}return window.NarwhalDialogs.open(name,{theme:S.theme,...defaults,...options});}
  function openPicker(x,y){openDialog('picker',{title:'Choose object',anchor:{x,y},candidates:[{label:'Sample curve — Curve',key:'curve'},{label:'Sample body — Edge 1',key:'edge'},{label:'Sample body — Face 1',key:'face'}],onHover:item=>{S.hoverCandidate=item;paintAll();},onSelect:()=>{S.selected=true;S.hoverCandidate=null;renderPanel();paintAll();},onClose:()=>{S.hoverCandidate=null;paintAll();}});}
  function showWelcome(tab='Recent'){closeMenus();closeAISettings();window.NarwhalDialogs.close();window.NarwhalWelcome.open({theme:S.theme,tab,ready:true,onNew:unit=>{S.units=({millimeters:'Millimeters',centimeters:'Centimeters',meters:'Meters',inches:'Inches',feet:'Feet'})[unit]||unit;renderStatus();},onOpen:()=>openDialog('open')});}
  function openInventory(){closeMenus();const guide=$('inventory');guide.replaceChildren();const head=el('div','guide-head');head.append(el('h1','','Narwhal 3D · Interface preview'),button('×',()=>guide.close()));guide.append(head,el('p','guide-note','EDITABLE UI PREVIEW — real source labels, metrics, palettes and artwork; synthetic document state. No CAD, provider calls, file changes or application settings. Press F1 to return here. Browser font rasterization, native OS dialogs and the display-only grid are not pixel-identical to the runtime.'));
    const controls=el('div','guide-controls');const theme=el('label','','Theme '),select=el('select');for(const n of ['light','dark']){const o=el('option','',n);o.value=n;select.append(o);}select.value=S.theme;select.onchange=()=>applyTheme(select.value);theme.append(select);controls.append(theme);const scale=el('label','','Interface scale '),sc=el('select');for(const n of ['auto','0.75','0.95','1','1.25','1.5']){const o=el('option','',n);o.value=n;sc.append(o);}sc.value=S.scaleMode;sc.onchange=()=>{S.scaleMode=sc.value;sizeWorkspace();};scale.append(sc);controls.append(scale);const sample=button(S.sample?'Clear sample scene':'Show sample selection',()=>{S.sample=!S.sample;S.selected=S.sample;renderPanel();paintAll();guide.close();});controls.append(sample,button('Workspace',()=>{guide.close();window.NarwhalWelcome.close();}),button('Export design state',exportDesign));guide.append(controls);
    const grid=el('div','guide-grid');const tile=(label,source,action)=>{const b=button(label,()=>{guide.close();action();});b.append(el('small','guide-source',source));grid.append(b);};
    for(const tab of ['New','Recent','Open','About'])tile('Welcome · '+tab,'native/welcome/src/main.rs',()=>showWelcome(tab));
        tile('Welcome · Loading','native/welcome/src/main.rs',()=>window.NarwhalWelcome.open({theme:S.theme,tab:'Recent',ready:false,loading:true,elapsed:1}));
        tile('Welcome · Recent examples','native/welcome/src/main.rs',()=>window.NarwhalWelcome.open({theme:S.theme,tab:'Recent',ready:true,recent:[{name:'Sample model.3dm',path:'/preview/Sample model.3dm'}],onOpen:()=>openDialog('open')}));
    for(const t of D.inspector_tabs)tile(t.name,'narwhal3d/ui.py / rightpanel.py',()=>{window.NarwhalWelcome.close();setTab(t.name);});
    tile('AI Assistant Settings','ai_settings_popup.py',openAISettings);tile('Category selector','toolbars.py',()=>$('left-header').click());tile('Point flyout','toolbars.py',()=>{const item=D.categories[0].tools.find(t=>t.flyout);if(item)openFlyout(item,document.querySelector('.left').getBoundingClientRect());});tile('Viewport menu','views.py / viewport_menu_data.py',()=>document.querySelector('.title-arrow').click());
    for(const item of window.NarwhalDialogs.catalog())tile(item.title||item.name||item.id,(item.sources||[]).join(', ')||'Application interface',()=>item.name==='picker'?openPicker(innerWidth/2,innerHeight/2):openDialog(item.id||item.name));
    for(const [kind,rows] of Object.entries(D.context_menus||{}))tile(kind+' context menu','navigate.py',()=>openMenu(rows,innerWidth/2,innerHeight/3));
        tile('Gumball menu','gumball.py',()=>openGumballMenu(innerWidth/2,innerHeight*.8));
        for(const m of D.menus)tile('Menu · '+m.label,'menubar_resource_data.py',()=>[...$('menubar').children].find(n=>n.textContent===m.label)?.click());
    const details=el('details');details.append(el('summary','','Reference image and fidelity notes'));const reference=el('img','reference-image');reference.src='playground/references/workspace-light.png';reference.alt='Actual application light workspace from the isolated UI report';details.append(reference);for(const item of window.NarwhalDialogs.catalog()){details.append(el('h3','',item.title));for(const note of item.differences||[])details.append(el('p','guide-note',note));}guide.append(details);
        guide.append(grid,el('p','guide-note','Edit playground/style.css, app.js, welcome.* and dialogs.*. data/ui_data.json is the exact exported snapshot. The coverage document distinguishes editable replicas from OS-owned approximations and source-only items; no source-only interface is claimed as recreated.'));
    if(!guide.open)guide.showModal();
  }
  function exportDesign(){const data={schema:1,kind:'narwhal-interface-design-state',theme:S.theme,scale:S.scaleMode,dockWidth:S.dockWidth,category:S.category,tab:S.tab,tokens:D.themes};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download='narwhal-ui-design.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function wireResizer(){const handle=document.querySelector('.dock-resize');let start=null;handle.onpointerdown=e=>{start={x:e.clientX,width:S.dockWidth};handle.setPointerCapture(e.pointerId);};handle.onpointermove=e=>{if(!start)return;S.dockWidth=Math.max(230,Math.min(600,start.width+(start.x-e.clientX)/S.scale));document.documentElement.style.setProperty('--dock-width',S.dockWidth+'px');paintAll();};handle.onpointerup=()=>start=null;handle.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();S.dockWidth=Math.max(230,Math.min(600,S.dockWidth+(e.key==='ArrowLeft'?10:-10)));sizeWorkspace();}};}
  document.addEventListener('pointerdown',e=>{if(popups.length&&!e.target.closest('.popup-menu')&&!e.target.closest('.menubar')&&!e.target.closest('.title-arrow')&&!e.target.closest('#left-header'))closeMenus();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){if($('inventory').open){$('inventory').close();return;}if(!$('ai-settings').hidden){closeAISettings();return;}if(popups.length){closeMenus();e.stopPropagation();}}});
  window.addEventListener('resize',sizeWorkspace);
  window.NarwhalPreview={state:S,openInventory,openAISettings,openDialog,showWelcome,setTab,setCategory,applyTheme,openPicker,snapshot:()=>({theme:S.theme,scale:S.scale,tab:S.tab,category:S.category,viewports:[...$('viewports').children].map(v=>({name:v.dataset.view,rect:v.getBoundingClientRect().toJSON()})),popupCount:popups.length})};
  applyTheme(S.theme);sizeWorkspace();renderMenubar();renderCategories();renderTools();renderOsnap();renderViewports();renderRail();renderPanel();renderStatus();renderHistory();wireConsole();wireResizer();setPrompt('Command:');
  new ResizeObserver(()=>requestAnimationFrame(paintAll)).observe($('viewports'));
  document.fonts.ready.then(paintAll);
  const screen=params.get('screen');if(screen==='guide')openInventory();else if(screen==='ai-settings')openAISettings();else if(screen&&D.inspector_tabs.some(t=>t.name.toLowerCase()===screen.toLowerCase()))setTab(D.inspector_tabs.find(t=>t.name.toLowerCase()===screen.toLowerCase()).name);else if(screen&&screen!=='workspace')openDialog(screen);else if(screen!=='workspace')showWelcome('Recent');
})();
