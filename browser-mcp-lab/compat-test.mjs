import assert from 'node:assert/strict';
import http from 'node:http';
import { BrowserController } from './server.mjs';
const browser=new BrowserController();
const checks=[];
const check=(name)=>{checks.push(name);console.log('PASS '+name);};
const html='<!doctype html><meta charset="utf-8"><title>Compatibility fixture</title>'+
'<style>body{margin:20px;font:16px sans-serif}canvas{display:block;border:1px solid;width:300px;height:100px}#blockedWrap{position:relative;width:140px;height:40px}#cover{position:absolute;inset:0;background:#aaa}#scroller{height:90px;width:300px;overflow:auto;border:1px solid}#far{margin-top:1500px}</style>'+
'<button id="pointer">Pointer button</button><output id="status">waiting</output>'+
'<label>Tracked input<input id="input"></label><output id="mirror"></output>'+
'<label>Readonly<input readonly id="readonly" value="keep"></label>'+
'<textarea aria-label="Area">initial</textarea><div contenteditable aria-label="Rich editor" role="textbox">old</div>'+
'<select aria-label="Choice"><option value="a">A</option><option value="b">B</option></select>'+
'<div id="blockedWrap"><button id="blocked">Blocked</button><div id="cover"></div></div>'+
'<canvas id="canvas" width="300" height="100" aria-label="Drawing"></canvas><output id="canvasResult"></output>'+
'<div id="scroller"><div style="height:600px">Scrollable panel</div></div>'+
'<button id="far">Far button</button><output id="farResult"></output>'+
'<script>'+
'window.count=0;window.down=false;const p=document.querySelector("#pointer");p.addEventListener("pointerdown",e=>{window.down=e.isTrusted});p.addEventListener("click",e=>{if(window.down&&e.isTrusted){window.count++;document.querySelector("#status").textContent="Pointer OK "+window.count;}window.down=false;});'+
'const input=document.querySelector("#input");const native=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value");Object.defineProperty(input,"value",{get(){return native.get.call(this)},set(v){native.set.call(this,v);window.programmatic=true}});input.addEventListener("input",e=>{document.querySelector("#mirror").textContent=input.value;window.lastInputTrusted=e.isTrusted;});'+
'document.querySelector("#blocked").onclick=()=>window.blockedClicked=true;'+
'const c=document.querySelector("#canvas");window.moves=0;c.addEventListener("pointerdown",e=>{window.canvasDown=e.isTrusted;c.setPointerCapture(e.pointerId)});c.addEventListener("pointermove",e=>{if(e.buttons===1)window.moves++});c.addEventListener("pointerup",e=>{document.querySelector("#canvasResult").textContent="Canvas "+window.moves;window.canvasReleased=e.buttons===0;});'+
'document.querySelector("#far").onclick=()=>document.querySelector("#farResult").textContent="Far OK";'+
'</script>';
const server=http.createServer((req,res)=>{res.writeHead(200,{'content-type':'text/html; charset=utf-8'});res.end(html)});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const url='http://127.0.0.1:'+server.address().port;
async function value(expression){return await browser.evaluate({expression});}
async function index(role,name){
 await browser.axGet({max_elements:500});
 const i=browser.axState.nodes.findIndex(n=>browser._axValue(n,'role')===role&&browser._axValue(n,'name')===name);
 assert(i>=0,role+' '+name+' missing');return i;
}
async function point(id){
 return await value('(function(){const r=document.getElementById('+JSON.stringify(id)+').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
}
try{
 await browser.launch({url});
 await browser.waitFor({text:'Pointer button'});
 await browser.axClick({element_index:await index('button','Pointer button')});
 assert.equal(await value('window.count'),1);check('full trusted pointer input');
 await browser.axSetValue({element_index:await index('textbox','Tracked input'),value:'中文🙂'});
 assert.equal(await value('document.querySelector("#mirror").textContent'),'中文🙂');
 assert.equal(await value('window.lastInputTrusted'),true);check('framework-style input state and trusted Unicode events');
 await browser.axSetValue({element_index:await index('textbox','Area'),value:''});
 assert.equal(await value('document.querySelector("textarea").value'),'');check('clear textarea');
 await browser.axSetValue({element_index:await index('textbox','Rich editor'),value:'可编辑文本'});
 assert.equal(await value('document.querySelector("[contenteditable]").textContent'),'可编辑文本');check('contenteditable text');
 await browser.axSetValue({element_index:await index('combobox','Choice'),value:'b'});
 assert.equal(await value('document.querySelector("select").value'),'b');check('native select value');
 await assert.rejects(browser.axSetValue({element_index:await index('textbox','Readonly'),value:'no'}),/read-only/);
 assert.equal(await value('document.querySelector("#readonly").value'),'keep');check('read-only editor rejection');
 await assert.rejects(browser.axClick({element_index:await index('button','Blocked')}),/covered/);
 assert.notEqual(await value('window.blockedClicked'),true);check('overlay rejection without click');
 await browser.axClick({element_index:await index('button','Far button')});
 assert.equal(await value('document.querySelector("#farResult").textContent'),'Far OK');check('scroll into view before element click');
 await value('scrollTo(0,0)');await new Promise(r=>setTimeout(r,100));
 let shot=await browser.screenshot(), canvas=await point('canvas');
 await browser.mouseClick({screenshot_id:shot.screenshotId,...canvas});
 assert.equal(await value('window.canvasDown'),true);
 await assert.rejects(browser.mouseClick({screenshot_id:shot.screenshotId,...canvas}),/fresh screenshot/);check('canvas coordinate click and stale screenshot rejection');
 shot=await browser.screenshot();
 await browser.mouseDrag({screenshot_id:shot.screenshotId,from_x:canvas.x-70,from_y:canvas.y,to_x:canvas.x+70,to_y:canvas.y,duration_ms:150});
 assert((await value('window.moves'))>=10);assert.equal(await value('window.canvasReleased'),true);check('canvas pointer drag and button release');
 shot=await browser.screenshot();const panel=await point('scroller');
 await browser.mouseScroll({screenshot_id:shot.screenshotId,...panel,delta_y:200});
 await new Promise(r=>setTimeout(r,200));
 assert((await value('document.querySelector("#scroller").scrollTop'))>0);check('nested panel wheel scroll');
 shot=await browser.screenshot();
 await assert.rejects(browser.mouseClick({screenshot_id:shot.screenshotId,x:-1,y:0}),/Coordinates/);
 await browser.mouseMove({screenshot_id:shot.screenshotId,...canvas});check('coordinate bounds and hover');
 await browser.axClick({element_index:await index('textbox','Tracked input')});
 await browser.axPressKey({key:'Control+a'});
 await browser.axTypeText({text:'replacement'});
 assert.equal(await value('document.querySelector("#input").value'),'replacement');check('keyboard chord selection and typing');
 await value('setTimeout(()=>{document.querySelector("#status").textContent="Delayed ready"},200)');
 await browser.waitFor({text:'Delayed ready',timeout_ms:2000});
 await assert.rejects(browser.waitFor({text:'impossible sentinel',timeout_ms:150}),/timeout/);check('bounded wait and application result check');
 const newTab=await browser.tabNew({url});
 await browser.tabClose({id:newTab.id});
 assert.equal((await browser.tabsList()).length,1);check('close active tab and recover remaining tab');
 console.log(JSON.stringify({ok:true,checks:checks.length,passed:checks},null,2));
}finally{
 await browser.close();
 await new Promise(r=>server.close(r));
}

