from pathlib import Path
import json,numpy as np
from Bio.PDB import MMCIFParser,PDBParser
root=Path(r'D:\26年项目\LLM-tRNA\af3_cca4_20261019_all')
results={}
for k in ['DA1110','DA0940','DL2150','DX1480']:
 seq=json.loads((root/'AF3_raw_and_PDB/inputs'/f'{k}.json').read_text())['sequences'][0]['rna']['sequence']
 cif=MMCIFParser(QUIET=True).get_structure(k,root/f'AF3_raw_and_PDB/{k}/cif/{k}_model.cif')
 raw=PDBParser(QUIET=True).get_structure(k,root/f'AF3_raw_and_PDB/{k}/pdb/{k}_model.pdb')
 aligned=PDBParser(QUIET=True).get_structure(k,root/f'reference_aligned/structures/{k}.pdb')
 def atoms(s):return {(a.parent.id[1],a.name):a for a in s.get_atoms()}
 ca,ra,aa=map(atoms,[cif,raw,aligned])
 keys=sorted(set(ca)&set(ra)&set(aa))
 x=np.array([ca[t].coord for t in keys],dtype=float);y=np.array([aa[t].coord for t in keys],dtype=float)
 z=np.array([ra[t].coord for t in keys],dtype=float)
 xc=x-x.mean(0);yc=y-y.mean(0)
 u,sv,vt=np.linalg.svd(xc.T@yc);d=np.eye(3);d[-1,-1]=np.linalg.det(u@vt);rot=u@d@vt
 err=np.linalg.norm(xc@rot-yc,axis=1)
 res=list(cif.get_residues());outseq=''.join(r.resname.strip() for r in res)
 last=[r.id[1] for r in res[-3:]]
 tail=[i for i,t in enumerate(keys) if t[0] in last]
 conf=json.loads((root/f'AF3_raw_and_PDB/{k}/{k}_summary_confidences.json').read_text())
 results[k]={'input_length':len(seq),'output_length':len(res),'sequence_exact_match':seq==outseq,'atoms_cif_pdb_aligned':[len(ca),len(ra),len(aa)],'shared_atoms':len(keys),'cif_to_raw_pdb_max_error_A':float(np.linalg.norm(x-z,axis=1).max()),'cif_to_aligned_rigid_fit_rmsd_A':float(np.sqrt(np.mean(err**2))),'rigid_fit_max_error_A':float(err.max()),'tail_fit_rmsd_A':float(np.sqrt(np.mean(err[tail]**2))),'tail':[(r.id[1],r.resname) for r in res[-3:]],'terminal3_mean_plddt':float(np.mean([a.bfactor for r in res[-3:] for a in r])),'ptm':conf['ptm']}
print(json.dumps(results,indent=2))
(root/'figure_cca/af3_integrity_check.json').write_text(json.dumps(results,indent=2))

