from pathlib import Path
import sys,json,csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon,Rectangle
from matplotlib.lines import Line2D
from scipy.spatial import ConvexHull
OUT=Path(__file__).resolve().parent
ROOT=OUT.parent
sys.path.insert(0,str(Path.home()/'.agents/skills/nature-figure/scripts'))
from audit_panel_alignment import require_matplotlib_panel_alignment
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':7,'pdf.fonttype':42,'svg.fonttype':'none'})
IDS=['DA1110','DA0940','DL2150','DX1480']
COLORS=dict(zip(IDS,['#167D9A','#D99524','#9460A4','#C65B55']))
GRAY='#79828B'
def read(path):
 r={}
 for l in path.read_text().splitlines():
  if l.startswith('ATOM') and l[17:20].strip() in ['A','C','G','U']:
   n=int(l[22:26]); a=l[12:16].strip()
   r.setdefault(n,{'name':l[17:20].strip(),'atoms':{}})['atoms'][a]=np.array([float(l[30:38]),float(l[38:46]),float(l[46:54])])
 return r
models={k:read(ROOT/'reference_aligned/structures'/f'{k}.pdb') for k in IDS}
ref=read(ROOT/'reference_aligned/structures/5ud5_chainD_superposed.pdb')
rings={'A':['N9','C8','N7','C5','C6','N1','C2','N3','C4'],'G':['N9','C8','N7','C5','C6','N1','C2','N3','C4'],'C':['N1','C2','N3','C4','C5','C6'],'U':['N1','C2','N3','C4','C5','C6']}
origin=np.mean([ref[n]['atoms']["C3'"] for n in range(71,77)],axis=0)
v=ref[76]['atoms']["C3'"]-ref[71]['atoms']["C3'"];v/=np.linalg.norm(v)
b=np.array([ref[76]['atoms'][a] for a in rings['A']]);normal=np.linalg.svd(b-b.mean(0))[2][-1];normal-=v*np.dot(v,normal);normal/=np.linalg.norm(normal)
u=np.cross(v,normal);rot=np.array([u,v,normal]).T
def p(x):return (np.asarray(x)-origin)@rot
def trace(ax,r,c,lw=.6,alpha=1):
 q=p([s['atoms']["C3'"] for s in r.values()]);ax.plot(q[:,0],q[:,1],color=c,lw=lw,alpha=alpha)
def sticks(ax,r,nums,c,alpha=1):
 seg=[];width=[]
 for n in nums:
  s=r[n];at=s['atoms'];ks=list(at)
  for i,a in enumerate(ks):
   for b in ks[i+1:]:
    if .8<np.linalg.norm(at[a]-at[b])<1.9:seg.append(p([at[a],at[b]])[:,:2]);width.append(1.05 if n in nums[-3:] else .55)
  if n+1 in nums and "O3'" in at and 'P' in r[n+1]['atoms']:
   pair=[at["O3'"],r[n+1]['atoms']['P']]
   if np.linalg.norm(pair[0]-pair[1])<2.1:seg.append(p(pair)[:,:2]);width.append(.6)
  if n in nums[-3:]:
   q=p([at[a] for a in rings[s['name']]])[:,:2];h=ConvexHull(q)
   ax.add_patch(Polygon(q[h.vertices],facecolor=c,edgecolor='none',alpha=.18,zorder=2))
 ax.add_collection(LineCollection(seg,colors=c,linewidths=width,alpha=alpha,zorder=3))
alltail=[a for r in [ref]+list(models.values()) for n in list(r)[-6:] for a in r[n]['atoms'].values()]
q=p(alltail)[:,:2];lo=q.min(0)-2;hi=q.max(0)+2;ctr=(lo+hi)/2;span=max(hi-lo)+2
lims=np.column_stack([ctr-span/2,ctr+span/2])
fig=plt.figure(figsize=(7.2047244094,5.5118110236))
gs=fig.add_gridspec(2,3,left=.035,right=.98,bottom=.16,top=.76,width_ratios=[1.28,1,1],wspace=.2,hspace=.38)
hero=fig.add_subplot(gs[:,0]);axes=[fig.add_subplot(gs[i//2,1+i%2]) for i in range(4)]
fig.text(.035,.965,'Structural comparison of tRNA 3′ ends',fontsize=12,weight='bold')
fig.text(.035,.925,'AF3 models overlaid with 5UD5 chain D | actual residue numbering',fontsize=7.3,color='#555D65')
handles=[Line2D([0],[0],color=GRAY,lw=1.5,label='Reference')]+[Line2D([0],[0],color=COLORS[k],lw=1.5,label=k) for k in IDS]
fig.legend(handles=handles,loc='upper left',bbox_to_anchor=(.025,.905),ncol=5,frameon=False,fontsize=6.5,handlelength=1.5,columnspacing=1.2)
trace(hero,ref,GRAY,.9)
for k in IDS:trace(hero,models[k],COLORS[k],.6,.75)
hero.add_patch(Rectangle(lo,*(hi-lo),fill=False,edgecolor='#3F464D',lw=.7,linestyle=(0,(3,3))))
hero.set_title('a   Whole-structure overlay',loc='left',fontsize=8,pad=12,weight='bold');hero.set_aspect('equal');hero.axis('off');hero.margins(.12)
for i,(ax,k) in enumerate(zip(axes,IDS)):
 r=models[k];nums=list(r)[-6:]
 sticks(ax,ref,list(ref)[-6:],GRAY,.8);sticks(ax,r,nums,COLORS[k])
 ax.set_xlim(*lims[0]);ax.set_ylim(*lims[1]);ax.set_aspect('equal',adjustable='box');ax.axis('off')
 ax.set_title(f'{chr(98+i)}   {k}',loc='left',fontsize=8,weight='bold',color=COLORS[k],pad=17)
 ax.text(0,1.025,'–'.join(r[n]['name']+str(n) for n in nums[-3:]),transform=ax.transAxes,fontsize=6.5,color=COLORS[k])
 ax.text(.02,.025,'Ref: C74–C75–A76',transform=ax.transAxes,fontsize=5.6,color=GRAY)
 x=lims[0,1]-10;y=lims[1,0]+3
 ax.plot([x,x+5],[y,y],color='#303840',lw=1);ax.text(x+2.5,y+1.2,'5 Å',ha='center',fontsize=5.5)
fig.text(.035,.105,'Close-ups: reference residues 71–76 and the final six residues of each model.',fontsize=6.5)
fig.text(.035,.075,'Sticks + translucent base planes; identical projection and scale in b–e. No local end fitting.',fontsize=6.5)
fig.text(.035,.045,'Supplied aligned coordinates; fitting-residue selection was not documented. One AF3 model per sequence.',fontsize=5.8,color='#59616A')
require_matplotlib_panel_alignment(fig,axes=axes,panel_ids=['b','c','d','e'],json_out=OUT/'cca_comparison.alignment.json',strict=True)
fig.savefig(OUT/'cca_comparison.pdf',facecolor='white')
fig.savefig(OUT/'cca_comparison.svg',facecolor='white')
fig.savefig(OUT/'cca_comparison.png',dpi=300,facecolor='white')
fig.savefig(OUT/'cca_comparison.tiff',dpi=600,facecolor='white')
rows=[{'structure':k,'residue':n,'base':r[n]['name'],'last_three':n in list(r)[-3:]} for k,r in [('Reference',ref)]+list(models.items()) for n in list(r)[-6:]]
with (OUT/'residue_labels.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(OUT/'projection.json').write_text(json.dumps({'origin':origin.tolist(),'rotation':rot.tolist(),'closeup_limits':lims.tolist(),'local_fit':False},indent=2))
(OUT/'README.md').write_text('''# tRNA 3′ end comparison

The figure compares physical RNA ends in the supplied aligned coordinate frame. All five structures are retained. Panel a uses C3′ backbone traces; b–e use distance-inferred covalent heavy-atom sticks for the final six residues, with translucent ring footprints on the final three bases. All close-ups use identical orthographic projection and scale; no local fitting or distortion was applied. Colour denotes identity, not confidence.

Reference: C71–C72–G73–C74–C75–A76. Actual model residue numbers are retained. DL2150 ends in C82–C83–A84; DX1480 ends in C72–A73–A74 (CAA). These are physical end comparisons, not validated homologous mappings.

The supplied alignment has no documented fitting-residue/atom selection. This is a reviewable visualization of existing coordinates, not a verified core-only fit. One selected AF3 model per sequence is shown, without ensemble uncertainty or statistical claims.

Bond criteria: intra-residue heavy-atom distances 0.8–1.9 Å; inter-residue O3′–P distance <2.1 Å. Base footprints are projected convex hulls of ring atoms. The whole-structure view intentionally has a different scale from the four close-ups.

Reproduce using render_cca.py with numpy, scipy, matplotlib and the nature-figure alignment auditor. PDF/SVG have editable text; PNG is 300 dpi, TIFF 600 dpi. residue_labels.csv and projection.json record exact selections and projection.
''',encoding='utf-8')
print(OUT/'cca_comparison.png')

