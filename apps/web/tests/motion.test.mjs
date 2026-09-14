import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function compile(name, globals={}) {
 const exports={};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/lib/motion/'+name,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports,...globals});
 return exports;
}
const {ContinuityTrack}=compile('continuity.ts');
function setup(reduced=false) {
 const effects=[], frames=new Map();let now=1000,id=0;
 const media={matches:reduced,addEventListener:(_,f)=>media.change=f,removeEventListener:()=>{}};
 const dialog={open:false,style:{removeProperty(k){delete this[k]}},showModal(){this.open=true},close(){this.open=false}};
 const exports=compile('index.ts',{performance:{now:()=>now},requestAnimationFrame:f=>{frames.set(++id,f);return id},cancelAnimationFrame:i=>frames.delete(i),window:{matchMedia:()=>media,addEventListener:()=>{},removeEventListener:()=>{}},document:{hidden:false,addEventListener:()=>{},removeEventListener:()=>{}},getComputedStyle:()=>({getPropertyValue:()=> '260ms'}),require:n=>n==='./continuity'?{ContinuityTrack}:{useRef:v=>({current:v}),useCallback:f=>f,useEffect:f=>effects.push(f)}});
 const hook=exports.useMotionDialog();hook.ref.current=dialog;const cleanup=effects.map(f=>f());
 return {hook,dialog,media,exports,cleanup,frames,tick(ms){now+=ms;const callbacks=[...frames.values()];frames.clear();callbacks.forEach(f=>f(now));}};
}
test('retarget preserves position velocity and acceleration at event time',()=>{
 const track=new ContinuityTrack([0]);track.target([1],0,.26);
 const before=track.sample(.09)[0];assert.ok(before.v>0);
 track.target([0],.09,.18);const after=track.sample(.09)[0];
 for(const k of ['p','v','a'])assert.equal(after[k],before[k]);
 assert.equal(track.sample(1)[0].p,0);
});
test('sampling is independent of frame rate and dropped frames',()=>{
 for(const hz of [60,90,120]){const track=new ContinuityTrack([0]);track.target([1],0,.26);for(let t=0;t<.1;t+=1/hz)track.sample(t);assert.ok(Math.abs(track.sample(.13)[0].p-.5)<1e-10);assert.equal(track.sample(5)[0].p,1);}
});
test('dialog retains focus trap until exit completes',()=>{
 const s=setup();s.hook.open();s.tick(300);s.hook.close();assert.equal(s.dialog.open,true);s.tick(300);assert.equal(s.dialog.open,false);assert.equal(s.frames.size,0);
});
test('reopening an exiting dialog preserves pose and cancels obsolete close',()=>{
 const s=setup();s.hook.open();s.tick(300);s.hook.close();s.tick(60);const pose=s.dialog.style.transform;s.hook.open();assert.equal(s.dialog.style.transform,pose);s.tick(300);assert.equal(s.dialog.open,true);assert.equal(s.frames.size,0);
});
test('reduced motion is immediate and disables smooth scroll',()=>{
 const s=setup(true);s.hook.open();s.hook.close();assert.equal(s.dialog.open,false);assert.equal(s.frames.size,0);assert.equal(s.exports.motionScrollBehavior(),'auto');
});
test('reduced motion change settles active exit and disposal cancels frames',()=>{
 const s=setup();s.hook.open();s.hook.close();s.media.matches=true;s.media.change();assert.equal(s.dialog.open,false);assert.equal(s.frames.size,0);
 const other=setup();other.hook.open();other.cleanup.forEach(f=>f());assert.equal(other.frames.size,0);
});

function extentSetup({ reduced = false } = {}) {
 const refs=[],frames=new Map(); let index=0, effect, cleanup, observer, now=1000, height=100, width=300, nextFrame=0;
 const media={matches:reduced,addEventListener:()=>{},removeEventListener:()=>{}};
 const attributes=new Set();
 const box={style:{},setAttribute:n=>attributes.add(n),removeAttribute:n=>attributes.delete(n)};
 const content={getBoundingClientRect:()=>({height,width})};
 const exports=compile('extent.ts',{
  performance:{now:()=>now},requestAnimationFrame:f=>{frames.set(++nextFrame,f);return nextFrame},cancelAnimationFrame:i=>frames.delete(i),
  ResizeObserver:class {constructor(f){observer=f}observe(){}disconnect(){}},
  window:{matchMedia:()=>media},document:{hidden:false,addEventListener:()=>{},removeEventListener:()=>{}},getComputedStyle:()=>({getPropertyValue:()=> '280ms'}),
  require:n=>n==='./continuity'?{ContinuityTrack}:n==='./index'?{reducedMotion:()=>media.matches}:{useRef:v=>refs[index++]??(refs[index-1]={current:v}),useLayoutEffect:f=>{effect=f}}
 });
 function render(open=true){cleanup?.();index=0;const result=exports.useMotionExtent(open);result.outer.current=box;result.inner.current=content;cleanup=effect();}
 render();
 return {box,frames,attributes,render,dispose:()=>cleanup?.(),resize(h,w=width){height=h;width=w;observer()},tick(ms){now+=ms;const callbacks=[...frames.values()];frames.clear();callbacks.forEach(f=>f(now));}};
}
test('extent ignores unchanged measurements and settles back to intrinsic height',()=>{
 const s=extentSetup();assert.equal(s.box.style.height,'auto');s.resize(100);assert.equal(s.frames.size,0);
 s.resize(200);assert.equal(s.box.style.height,'100px');s.tick(140);assert.ok(parseFloat(s.box.style.height)>100);s.tick(140);assert.equal(s.box.style.height,'auto');assert.equal(s.frames.size,0);s.dispose();
});
test('extent retargeting preserves current pose and width changes snap',()=>{
 const s=extentSetup();s.resize(200);s.tick(100);const pose=s.box.style.height;s.resize(140);assert.equal(s.box.style.height,pose);
 s.resize(150,250);assert.equal(s.box.style.height,'auto');assert.equal(s.frames.size,0);s.dispose();
});
test('disclosure target reversal preserves height and finishes closed',()=>{
 const s=extentSetup();s.render(false);s.tick(100);const pose=s.box.style.height;s.render(true);assert.equal(s.box.style.height,pose);s.tick(300);s.render(false);s.tick(300);assert.equal(s.box.style.height,'0px');s.dispose();
});
test('extent reduced motion snaps and unmount cancels pending frame',()=>{
 const s=extentSetup({reduced:true});s.resize(200);assert.equal(s.frames.size,0);assert.equal(s.box.style.height,'auto');s.dispose();
 const active=extentSetup();active.resize(200);assert.equal(active.frames.size,1);active.dispose();assert.equal(active.frames.size,0);
});
