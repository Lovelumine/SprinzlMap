from pathlib import Path
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon, Rectangle
from scipy.spatial import ConvexHull
from scipy.interpolate import CubicSpline
OUT=Path(__file__).resolve().parent
ROOT=OUT.parent
sys.path.insert(0,str(Path.home()/'.agents/skills/nature-figure/scripts'))
from audit_panel_alignment import require_matplotlib_panel_alignment
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':7,'pdf.fonttype':42,'svg.fonttype':'none'})
IDS=['DA1110','DA0940','DL2150','DX1480']
COLORS=['#167D9A','#CD891B','#8952A4','#CB5750']
GRAY='#697780'
def read(path):
 r={}
 for l in path.read_text().splitlines():
  if l.startswith('ATOM') and l[17:20].strip() in ['A','C','G','U']:
   r.setdefault(int(l[22:26]),{'name':l[17:20].strip(),'atoms':{}})['atoms'][l[12:16].strip()]=np.array([float(l[30:38]),float(l[38:46]),float(l[46:54])])
 return r
ref=read(ROOT/'reference_aligned/structures/5ud5_chainD_superposed.pdb')
models={k:read(ROOT/'reference_aligned/structures'/f'{k}.pdb') for k in IDS}
rings={'A':['N9','C8','N7','C5','C6','N1','C2','N3','C4'],'G':['N9','C8','N7','C5','C6','N1','C2','N3','C4'],'C':['N1','C2','N3','C4','C5','C6'],'U':['N1','C2','N3','C4','C5','C6']}
origin=np.mean([ref[n]['atoms']["C3'"] for n in range(71,77)],axis=0)
v=ref[76]['atoms']["C3'"]-ref[71]['atoms']["C3'"];v/=np.linalg.norm(v)
b=np.array([ref[76]['atoms'][a] for a in rings['A']]);normal=np.linalg.svd(b-b.mean(0))[2][-1];normal-=v*np.dot(v,normal);normal/=np.linalg.norm(normal)
u=np.cross(v,normal)
# One shared view, tilted 25 degrees to separate ring footprints from backbone.
angle=np.deg2rad(25);u2=u*np.cos(angle)+normal*np.sin(angle);rot=np.array([u2,v,np.cross(u2,v)]).T
def p(x):return (np.asarray(x)-origin)@rot
def center(r,n):return p(np.mean([r[n]['atoms'][a] for a in rings[r[n]['name']]],axis=0))[:2]
def trace(ax,r,c,lw=.65,alpha=1,nums=None,smooth=False):
 nums=list(r) if nums is None else nums
 q=p([r[n]['atoms']["C3'"] for n in nums])
 if smooth:
  full=p([s['atoms']["C3'"] for s in r.values()])
  t=np.r_[0,np.cumsum(np.linalg.norm(np.diff(full,axis=0),axis=1))]
  assert np.all(np.diff(t)>0)
  curve=CubicSpline(t,full,axis=0,bc_type='natural')
  assert np.allclose(curve(t),full)
  ix=[list(r).index(n) for n in nums]
  q=curve(np.linspace(t[ix[0]],t[ix[-1]],max(60,16*len(nums))))
 ax.plot(q[:,0],q[:,1],c=c,lw=lw,alpha=alpha,solid_capstyle='round')
def sticks(ax,r,nums,c,lw=1,alpha=1,planes=True):
 seg=[]
 for n in nums:
  at=r[n]['atoms'];ks=list(at)
  for i,a in enumerate(ks):
   for b in ks[i+1:]:
    if .8<np.linalg.norm(at[a]-at[b])<1.9:seg.append(p([at[a],at[b]])[:,:2])
  if n+1 in nums and "O3'" in at:
   pair=[at["O3'"],r[n+1]['atoms']['P']]
   if np.linalg.norm(pair[0]-pair[1])<2.1:seg.append(p(pair)[:,:2])
  if planes:
   q=p([at[a] for a in rings[r[n]['name']]])[:,:2]
   ax.add_patch(Polygon(q[ConvexHull(q).vertices],facecolor=c,edgecolor='none',alpha=.16*alpha,zorder=2))
 ax.add_collection(LineCollection(seg,colors=c,linewidths=lw,alpha=alpha,zorder=3))
# Crop includes reference six residues and model terminal three, with shared limits.
points=[a for n in range(71,77) for a in ref[n]['atoms'].values()]+[a for r in models.values() for n in list(r)[-3:] for a in r[n]['atoms'].values()]
q=p(points)[:,:2];mn=q.min(0);mx=q.max(0);ct=(mn+mx)/2
span=max(mx-mn)+15
lims=np.column_stack([ct-span/2,ct+span/2])
whole=p([r['atoms']["C3'"] for rs in [ref]+list(models.values()) for r in rs.values()])[:,:2]
wct=(whole.min(0)+whole.max(0))/2;wspan=max(whole.max(0)-whole.min(0))+12
fig=plt.figure(figsize=(10.2362204724,7.0866141732))
gs=fig.add_gridspec(2,4,left=.035,right=.985,bottom=.14,top=.83,hspace=.36,wspace=.12)
axes=[]
fig.text(.035,.96,'tRNA structures and resolved 3′-end comparisons',fontsize=13,weight='bold')
fig.text(.035,.917,'Each column: one AF3 model versus 5UD5 chain D. Grey = reference; colour = model.',fontsize=8)
for j,(k,c) in enumerate(zip(IDS,COLORS)):
 r=models[k];nums=list(r)[-3:]
 ax=fig.add_subplot(gs[0,j]);axes.append(ax)
 trace(ax,ref,GRAY,.8,.7,smooth=True);trace(ax,r,c,.7,.95,smooth=True);trace(ax,r,c,1.8,1,nums,smooth=True)
 ax.add_patch(Rectangle(mn-2,*(mx-mn+4),fill=False,edgecolor=GRAY,lw=.6,linestyle=(0,(3,3))))
 ax.set_xlim(wct[0]-wspan/2,wct[0]+wspan/2);ax.set_ylim(wct[1]-wspan/2,wct[1]+wspan/2)
 ax.set_aspect('equal');ax.axis('off')
 ax.set_title(f'{chr(97+j)}   {k}',loc='left',fontsize=9,color=c,weight='bold',pad=10)
 ax=fig.add_subplot(gs[1,j]);axes.append(ax)
 # Only a pale trace for upstream reference context: removes dense lower sticks.
 trace(ax,ref,GRAY,.65,.32,[71,72,73,74])
 sticks(ax,ref,[74,75,76],GRAY,.75,.6)
 sticks(ax,r,nums,c,1.2,1)
 ax.set_xlim(*lims[0]);ax.set_ylim(*lims[1]);ax.set_aspect('equal');ax.axis('off')
 ax.set_title(f'{chr(101+j)}   '+('CCA end' if k!='DX1480' else 'CAA end'),loc='left',fontsize=8,color=c,weight='bold',pad=9)
 # Fixed label rails: model on right, reference on left. Sorted anchors prevent crossing.
 for rr,nn,cc,side in [(r,nums,c,1),(ref,[74,75,76],GRAY,-1)]:
  ordered=sorted(nn,key=lambda n:center(rr,n)[1])
  ys=np.array([center(rr,n)[1] for n in ordered]);gap=4.8
  for t in range(1,len(ys)):ys[t]=max(ys[t],ys[t-1]+gap)
  ys-=max(0,ys[-1]-(lims[1,1]-4))
  x=lims[0,1]-1 if side==1 else lims[0,0]+1
  for n,y in zip(ordered,ys):
   xy=center(rr,n)
   ax.annotate(rr[n]['name']+str(n),xy=xy,xytext=(x,y),ha='right' if side==1 else 'left',va='center',fontsize=7,color=cc,arrowprops={'arrowstyle':'-','color':cc,'lw':.55,'shrinkA':2,'shrinkB':2},zorder=6)
 # Upstream reference identities in a dedicated footer, not on crowded atoms.
 ax.text(.02,-.045,'Ref context: C71–C72–G73',transform=ax.transAxes,fontsize=6,color=GRAY)
 x=lims[0,0]+2;y=lims[1,0]+1.5
 ax.plot([x,x+5],[y,y],c='#303840',lw=1);ax.text(x+2.5,y+1.4,'5 Å',ha='center',fontsize=6)
fig.text(.035,.087,'End close-ups: coloured sticks = the final three model residues; grey sticks = reference C74–C75–A76.',fontsize=7)
fig.text(.035,.059,'Reference 71–73 is retained as a faint backbone trace. Same view and scale within each row; no local end fitting.',fontsize=7)
fig.text(.035,.031,'Existing aligned coordinates; original fitting selection undocumented. Actual residue numbers retained. One AF3 model per sequence.',fontsize=6,color=GRAY)
require_matplotlib_panel_alignment(fig,axes=axes,panel_ids=['a','e','b','f','c','g','d','h'],json_out=OUT/'cca_comparison_v2.alignment.json',strict=True)
fig.savefig(OUT/'cca_comparison_v2.pdf',facecolor='white')
fig.savefig(OUT/'cca_comparison_v2.svg',facecolor='white')
fig.savefig(OUT/'cca_comparison_v2.png',dpi=300,facecolor='white')
fig.savefig(OUT/'cca_comparison_v2.tiff',dpi=600,facecolor='white')
(OUT/'v2_notes.md').write_text('Version 2: four separate whole-structure comparisons; each model physical terminal three residues shown as full sticks and directly labelled. Reference 74–76 grey sticks; reference 71–73 simplified to pale C3-prime trace, identities listed. Model upstream three residues intentionally omitted from close-ups to reduce obstruction; full model retained above. All coordinates unmodified. Same projection across panels; same scale within each row. Shared projection tilted by 25 degrees relative to v1 to reduce overlap. Actual sequence numbering, not established homologous mapping. Original alignment fit selection undocumented. Figure size 260 x 180 mm, suitable as a review plate; typography should be reassessed if reduced. Leader lines intentionally connect labels to base-ring centres. Original version preserved.',encoding='utf-8')
print('Version 2 exported')

