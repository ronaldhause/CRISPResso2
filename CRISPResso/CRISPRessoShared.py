'''
CRISPResso2 - Kendell Clement and Luca Pinello 2018
Software pipeline for the analysis of genome editing outcomes from deep sequencing data
(c) 2017 The General Hospital Corporation. All Rights Reserved.
'''

import argparse
from collections import defaultdict
import numpy as np
import os
import pandas as pd
import re
import string
import shutil
import signal
import subprocess as sb
import sys

from CRISPResso import cnwalign

running_python3 = False
if sys.version_info > (3, 0):
    running_python3 = True

if running_python3:
    import pickle as cp #python 3
else:
    import cPickle as cp #python 2.7

class CRISPRessoException(Exception):
    pass
class BadParameterException(Exception):
    pass
class OutputFolderIncompleteException(Exception):
    pass
class NTException(Exception):
    pass

__version__ = "2.0.09b"

##dict to lookup abbreviated params
crispresso_options_lookup = {
    'r1':'fastq_r1',
    'r2':'fastq_r2',
    'a':'amplicon_seq',
    'an':'amplicon_name',
    'amas':'amplicon_min_alignment_score',
    'g':'guide_seq',
    'e':'expected_hdr_amplicon_seq',
    'c':'coding_seq',
    'q':'min_average_read_quality',
    's':'min_single_bp_quality',
    'n':'name',
    'o':'output_folder',
    'w':'quantification_window_size',
    'wc':'quantification_window_center',
    }


def getCRISPRessoArgParser(_ROOT, parserTitle = "CRISPResso Parameters",requiredParams={}):
    parser = argparse.ArgumentParser(description=parserTitle,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-r1','--fastq_r1', type=str,  help='First fastq file',default='Fastq filename',required='fastq_r1' in requiredParams)
    parser.add_argument('-r2','--fastq_r2', type=str,  help='Second fastq file for paired end reads',default='')

    parser.add_argument('-a','--amplicon_seq', type=str,  help='Amplicon Sequence (can be comma-separated list of multiple sequences)', required='amplicon_seq' in requiredParams)

    parser.add_argument('-an','--amplicon_name', type=str,  help='Amplicon Name (can be comma-separated list of multiple names, corresponding to amplicon sequences given in --amplicon_seq', default='Reference')
    parser.add_argument('-amas','--amplicon_min_alignment_score', type=str,  help='Amplicon Minimum Alignment Score; score between 0 and 100; sequences must have at least this homology score with the amplicon to be aligned (can be comma-separated list of multiple scores, corresponding to amplicon sequences given in --amplicon_seq)', default="60")
    parser.add_argument('--default_min_aln_score','--min_identity_score',  type=int, help='Default minimum homology score for a read to align to a reference amplicon', default=60)
    parser.add_argument('--expand_ambiguous_alignments', help='If more than one reference amplicon is given, reads that align to multiple reference amplicons will count equally toward each amplicon. Default behavior is to exclude ambiguous alignments.', action='store_true')
    parser.add_argument('-g','--guide_seq', help="sgRNA sequence, if more than one, please separate by commas. Note that the sgRNA needs to be input as the guide RNA sequence (usually 20 nt) immediately adjacent to but not including the PAM sequence (5' of NGG for SpCas9). If the PAM is found on the opposite strand with respect to the Amplicon Sequence, ensure the sgRNA sequence is also found on the opposite strand. The CRISPResso convention is to depict the expected cleavage position using the value of the parameter '--quantification_window_center' nucleotides from the 3' end of the guide. In addition, the use of alternate nucleases besides SpCas9 is supported. For example, if using the Cpf1 system, enter the sequence (usually 20 nt) immediately 3' of the PAM sequence and explicitly set the '--cleavage_offset' parameter to 1, since the default setting of -3 is suitable only for SpCas9.", default='')
    parser.add_argument('-e','--expected_hdr_amplicon_seq', help='Amplicon sequence expected after HDR', default='')
    parser.add_argument('-c','--coding_seq',  help='Subsequence/s of the amplicon sequence covering one or more coding sequences for frameshift analysis. If more than one (for example, split by intron/s), please separate by commas.', default='')
    parser.add_argument('-q','--min_average_read_quality', type=int, help='Minimum average quality score (phred33) to keep a read', default=0)
    parser.add_argument('-s','--min_single_bp_quality', type=int, help='Minimum single bp score (phred33) to keep a read', default=0)
    parser.add_argument('--min_bp_quality_or_N', type=int, help='Bases with a quality score (phred33) less than this value will be set to "N"', default=0)
    parser.add_argument('-n','--name',  help='Output name', default='')
    parser.add_argument('--file_prefix',  help='File prefix for output plots and tables', default='')
    parser.add_argument('-o','--output_folder',  help='', default='')

    ## read preprocessing params
    parser.add_argument('--split_paired_end',help='Splits a single fastq file containing paired end reads in two files before running CRISPResso',action='store_true')
    parser.add_argument('--trim_sequences',help='Enable the trimming of Illumina adapters with Trimmomatic',action='store_true')
    parser.add_argument('--trimmomatic_options_string', type=str, help='Override options for Trimmomatic',default=' ILLUMINACLIP:%s:0:90:10:0:true MINLEN:40' % os.path.join(_ROOT, 'data', 'NexteraPE-PE.fa'))
    parser.add_argument('--min_paired_end_reads_overlap',  type=int, help='Parameter for the FLASH read merging step. Minimum required overlap length between two reads to provide a confident overlap. ', default=4)
    parser.add_argument('--max_paired_end_reads_overlap',  type=int, help='Parameter for the FLASH merging step.  Maximum overlap length expected in approximately 90%% of read pairs. Please see the FLASH manual for more information.', default=None)

    parser.add_argument('-w', '--quantification_window_size','--window_around_sgrna', type=int, help='Defines the size of the quantification window(s) centered around the position specified by the "--cleavage_offset" or "--quantification_window_center" parameter in relation to the provided guide RNA sequence (--sgRNA). Indels overlapping this quantification window are included in classifying reads as modified or unmodified. A value of 0 disables this window and indels in the entire amplicon are considered.', default=1)
    parser.add_argument('-wc','--quantification_window_center','--cleavage_offset', type=int, help="Center of quantification window to use within respect to the 3' end of the provided sgRNA sequence. Remember that the sgRNA sequence must be entered without the PAM. For cleaving nucleases, this is the predicted cleavage position. The default is -3 and is suitable for the Cas9 system. For alternate nucleases, other cleavage offsets may be appropriate, for example, if using Cpf1 this parameter would be set to 1. For base editors, this could be set to -17.", default=-3)
#    parser.add_argument('--cleavage_offset', type=str, help="Predicted cleavage position for cleaving nucleases with respect to the 3' end of the provided sgRNA sequence. Remember that the sgRNA sequence must be entered without the PAM. The default value of -3 is suitable for the Cas9 system. For alternate nucleases, other cleavage offsets may be appropriate, for example, if using Cpf1 this parameter would be set to 1. To suppress the cleavage offset, enter 'N'.", default=-3)
    parser.add_argument('--exclude_bp_from_left', type=int, help='Exclude bp from the left side of the amplicon sequence for the quantification of the indels', default=15)
    parser.add_argument('--exclude_bp_from_right', type=int, help='Exclude bp from the right side of the amplicon sequence for the quantification of the indels', default=15)

    parser.add_argument('--ignore_substitutions',help='Ignore substitutions events for the quantification and visualization',action='store_true')
    parser.add_argument('--ignore_insertions',help='Ignore insertions events for the quantification and visualization',action='store_true')
    parser.add_argument('--ignore_deletions',help='Ignore deletions events for the quantification and visualization',action='store_true')
    parser.add_argument('--discard_indel_reads',help='Discard reads with indels in the quantification window from analysis',action='store_true')

    parser.add_argument('--needleman_wunsch_gap_open',type=int,help='Gap open option for Needleman-Wunsch alignment',default=-20)
    parser.add_argument('--needleman_wunsch_gap_extend',type=int,help='Gap extend option for Needleman-Wunsch alignment',default=-2)
    parser.add_argument('--needleman_wunsch_gap_incentive',type=int,help='Gap incentive value for inserting indels at cut sites',default=1)
    parser.add_argument('--needleman_wunsch_aln_matrix_loc',type=str,help='Location of the matrix specifying substitution scores in the NCBI format (see ftp://ftp.ncbi.nih.gov/blast/matrices/)',default='EDNAFULL')
    parser.add_argument('--aln_seed_count',type=int,default=4,help=argparse.SUPPRESS)#help='Number of seeds to test whether read is forward or reverse',default=4)
    parser.add_argument('--aln_seed_len',type=int,default=6,help=argparse.SUPPRESS)#help='Length of seeds to test whether read is forward or reverse',default=6)
    parser.add_argument('--aln_seed_min',type=int,default=2,help=argparse.SUPPRESS)#help='number of seeds that must match to call the read forward/reverse',default=2)

    parser.add_argument('--keep_intermediate',help='Keep all the  intermediate files',action='store_true')
    parser.add_argument('--dump',help='Dump numpy arrays and pandas dataframes to file for debugging purposes',action='store_true')
    parser.add_argument('--plot_window_size','--offset_around_cut_to_plot',  type=int, help='Window around quantification window center to plot. Plots alleles centered at each guide.', default=40)
    parser.add_argument('--min_frequency_alleles_around_cut_to_plot', type=float, help='Minimum %% reads required to report an allele in the alleles table plot.', default=0.2)
    parser.add_argument('--max_rows_alleles_around_cut_to_plot',  type=int, help='Maximum number of rows to report in the alleles table plot. ', default=50)

    parser.add_argument('--conversion_nuc_from',  help='For base editor plots, this is the nucleotide targeted by the base editor',default='C')
    parser.add_argument('--conversion_nuc_to',  help='For base editor plots, this is the nucleotide produced by the base editor',default='T')

    parser.add_argument('--base_editor_output', help='Outputs plots and tables to aid in analysis of base editor studies.',action='store_true')
    parser.add_argument('-qwc','--quantification_window_coordinates', type=str, help='Bp positions in the amplicon sequence specifying the quantification window. This parameter overrides values of the "--quantification_window_center", "--cleavage_offset", "--window_around_sgrna" or "--window_around_sgrna" values. Any indels outside this window are excluded. Ranges are separted by the dash sign like "start-stop", and multiple ranges can be separated by the underscore (_). ' +
        'A value of 0 disables this filter. (can be comma-separated list of values, corresponding to amplicon sequences given in --amplicon_seq e.g. 5-10,5-10_20-30 would specify the 5th-10th bp in the first reference and the 5th-10th and 20th-30th bp in the second reference)', default=None)

    parser.add_argument('--crispresso1_mode', help='Parameter usage as in CRISPResso 1',action='store_true')
    parser.add_argument('--auto', help='Infer amplicon sequence from most common reads',action='store_true')
    parser.add_argument('--debug', help='Show debug messages', action='store_true')
    parser.add_argument('--no_rerun', help="Don't rerun CRISPResso2 if a run using the same parameters has already been finished.", action='store_true')
    parser.add_argument('--suppress_report',  help='Suppress output report', action='store_true')
    parser.add_argument('--write_cleaned_report', action='store_true',help=argparse.SUPPRESS)#trims working directories from output in report (for web access) 


    #depreciated params
    parser.add_argument('--save_also_png',default=False,help=argparse.SUPPRESS) #help='Save also .png images additionally to .pdf files') #depreciated

    return parser

#######
# Nucleotide functions
#######
nt_complement=dict({'A':'T','C':'G','G':'C','T':'A','N':'N','_':'_','-':'-'})
def reverse_complement(seq):
        return "".join([nt_complement[c] for c in seq.upper()[-1::-1]])

def reverse(seq):
    return "".join(c for c in seq.upper()[-1::-1])

def find_wrong_nt(sequence):
    return list(set(sequence.upper()).difference(set(['A','T','C','G','N'])))

def capitalize_sequence(x):
    return str(x).upper() if not pd.isnull(x) else x


######
# File functions
######

def clean_filename(filename):
    #get a clean name that we can use for a filename
    validFilenameChars = "+-_.() %s%s" % (string.ascii_letters, string.digits)

    cleanedFilename = unicodedata.normalize('NFKD', unicode(filename)).encode('ASCII', 'ignore')
    return ''.join(c for c in cleanedFilename if c in validFilenameChars)

def check_file(filename):
    try:
        with open(filename): pass
    except IOError:
        files_in_dir = os.listdir('.')
        raise BadParameterException("The specified file '"+filename + "' cannot be opened.\nAvailable files in current directory: " + str(files_in_dir))

def force_symlink(src, dst):

    if os.path.exists(dst) and os.path.samefile(src,dst):
        return

    try:
        os.symlink(src, dst)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            os.remove(dst)
            os.symlink(src, dst)
        elif exc.errno == errno.EPROTO:
            #in docker on windows 7, symlinks don't work so well, so we'll just copy the file.
            shutil.copyfile(src, dst)

def parse_count_file(fileName):
    if os.path.exists(fileName):
        with open(fileName) as infile:
            lines = infile.readlines()
            ampSeq = lines[0].rstrip().split("\t")
            ampSeq.pop(0) #get rid of 'Amplicon' at the beginning of line
            ampSeq = "".join(ampSeq)
            lab_freqs={}
            for i in range(1,len(lines)):
                line = lines[i].rstrip()
                lab_freq_arr = line.split()
                lab = lab_freq_arr.pop(0)
                lab_freqs[lab] = lab_freq_arr
        return ampSeq,lab_freqs
    else:
        print("Cannot find output file '%s'"%fileName)
        return None,None

def parse_alignment_file(fileName):
    if os.path.exists(fileName):
        with open(fileName) as infile:
            lines = infile.readlines()
            ampSeq = lines[0].rstrip().split("\t")
            ampSeq.pop(0) #get rid of 'Amplicon' at the beginning of line
            ampSeq = "".join(ampSeq)
            lab_freqs={}
            for i in range(1,len(lines)):
                line = lines[i].rstrip()
                lab_freq_arr = line.split()
                lab = lab_freq_arr.pop(0)
                lab_freqs[lab] = lab_freq_arr
        return ampSeq,lab_freqs
    else:
        print("Cannot find output file '%s'"%fileName)
        return None,None

def check_output_folder(output_folder):
    """
    Checks to see that the CRISPResso run has completed, and gathers the amplicon info for that run
    returns:
    - quantification file = CRISPResso_quantification_of_editing_frequency.txt for this run
    - amplicons = a list of amplicons analyzed in this run
    - amplicon_info = a dict of attributes found in quantification_file for each amplicon
    """
    run_file = os.path.join(output_folder,'CRISPResso2_info.pickle')
    if not os.path.exists(run_file):
        raise OutputFolderIncompleteException('The folder %s is not a valid CRISPResso2 output folder. Cannot find summary file %s.' % (output_folder,run_file))
    run_data = cp.load(open(run_file,'rb'))

    amplicon_info = {}
    amplicons = run_data['ref_names']

    quantification_file=run_data['quant_of_editing_freq_filename']
    if os.path.exists(quantification_file):
        with open(quantification_file) as quant_file:
            head_line = quant_file.readline()
            head_line_els = head_line.split("\t")
            for line in quant_file:
                line_els = line.split("\t")
                amplicon_name = line_els[0]
                amplicon_info[amplicon_name] = {}
                amplicon_quant_file = run_data['refs'][amplicon_name]['combined_pct_vector_filename']
                if not os.path.exists(amplicon_quant_file):
                    raise OutputFolderIncompleteException('The folder %s  is not a valid CRISPResso2 output folder. Cannot find quantification file %s for amplicon %s.' % (output_folder,amplicon_quant_file,amplicon_name))
                amplicon_info[amplicon_name]['quantification_file'] = amplicon_quant_file

                amplicon_mod_count_file = run_data['refs'][amplicon_name]['quant_window_mod_count_filename']
                if not os.path.exists(amplicon_mod_count_file):
                    raise OutputFolderIncompleteException('The folder %s  is not a valid CRISPResso2 output folder. Cannot find modification count vector file %s for amplicon %s.' % (output_folder,amplicon_mod_count_file,amplicon_name))
                amplicon_info[amplicon_name]['modification_count_file'] = amplicon_mod_count_file

                amplicon_info[amplicon_name]['allele_files'] = run_data['refs'][amplicon_name]['allele_frequency_files']

                for idx,el in enumerate(head_line_els):
                    amplicon_info[amplicon_name][el] = line_els[idx]

        return quantification_file,amplicons,amplicon_info
    else:
        raise OutputFolderIncompleteException("The folder %s  is not a valid CRISPResso2 output folder. Cannot find quantification file '%s'." %(output_folder,quantification_file))

def get_most_frequent_reads(fastq_r1,fastq_r2,number_of_reads_to_consider,max_paired_end_reads_overlap,min_paired_end_reads_overlap):
    """
    Gets the most frequent amplicon from a fastq file (or after merging a r1 and r2 fastq file)
    input:
    fastq_r1: path to fastq r1 (can be gzipped)
    fastq_r2: path to fastq r2 (can be gzipped)
    number_of_reads_to_consider: number of reads from the top of the file to examine
    min_paired_end_reads_overlap: min overlap in bp for flashing (merging) r1 and r2
    max_paired_end_reads_overlap: max overlap in bp for flashing (merging) r1 and r2

    returns:
    list of amplicon strings sorted by order in format:
    12345 AATTCCG
    124 ATATATA
    5 TTATA
    """
    view_cmd_1 = 'cat'
    if fastq_r1.endswith('.gz'):
        view_cmd_1 = 'zcat'
    file_generation_command = "%s %s | head -n %d "%(view_cmd_1,fastq_r1,number_of_reads_to_consider)

    if fastq_r2:
        view_cmd_2 = 'cat'
        if fastq_r2.endswith('.gz'):
            view_cmd_2 = 'zcat'
        max_overlap_param = ""
        if max_paired_end_reads_overlap:
            max_overlap_param = "--max-overlap="+str(max_paired_end_reads_overlap)
        file_generation_command = "bash -c 'paste <(%s %s) <(%s %s)' | head -n %d | paste - - - - | awk -v OFS=\"\\n\" -v FS=\"\\t\" '{print($1,$3,$5,$7,$2,$4,$6,$8)}' | flash - --interleaved-input %s --min-overlap %d --to-stdout 2>/dev/null " %(view_cmd_1,fastq_r1,view_cmd_2,fastq_r2,number_of_reads_to_consider,max_overlap_param,min_paired_end_reads_overlap)
    count_frequent_cmd = file_generation_command + " | awk '((NR-2)%4==0){print $1}' | sort | uniq -c | sort -nr "
    def default_sigpipe():
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    piped_commands = count_frequent_cmd.split("|")
    pipes = [None] * len(piped_commands)
    pipes[0] = sb.Popen(piped_commands[0],stdout=sb.PIPE,preexec_fn=default_sigpipe,shell=True)
    for pipe_i in range(1,len(piped_commands)):
        pipes[pipe_i] = sb.Popen(piped_commands[pipe_i],stdin=pipes[pipe_i-1].stdout,stdout=sb.PIPE,preexec_fn=default_sigpipe,shell=True)
    top_unaligned = pipes[-1].communicate()[0]

    if pipes[-1].poll() != 0:
        raise AutoException('Cannot retrieve most frequent amplicon sequences. Got nonzero return code.')
    seq_lines = top_unaligned.strip().split("\n")
    if len(seq_lines) == 0:
        raise AutoException('Cannot parse any frequent amplicons sequences.')
    return seq_lines

def guess_amplicons(fastq_r1,fastq_r2,number_of_reads_to_consider,max_paired_end_reads_overlap,min_paired_end_reads_overlap,aln_matrix,needleman_wunsch_gap_open,needleman_wunsch_gap_extend,min_freq_to_consider=0.01,amplicon_similarity_cutoff=0.95):
    """
    guesses the amplicons used in an experiment by examining the most frequent read (giant caveat -- most frequent read should be unmodified)
    input:
    fastq_r1: path to fastq r1 (can be gzipped)
    fastq_r2: path to fastq r2 (can be gzipped)
    number_of_reads_to_consider: number of reads from the top of the file to examine
    min_paired_end_reads_overlap: min overlap in bp for flashing (merging) r1 and r2
    max_paired_end_reads_overlap: max overlap in bp for flashing (merging) r1 and r2
    needleman_wunsch_gap_open: alignment penalty assignment used to determine similarity of two sequences
    needleman_wunsch_gap_extend: alignment penalty assignment used to determine similarity of two sequences
    min_freq_to_consider: selected ampilcon must be frequent at least at this percentage in the population
    amplicon_similarity_cutoff: if the current amplicon has similarity of greater than this cutoff to any other existing amplicons, it won't be added

    returns:
    list of putative amplicons
    """
    seq_lines = get_most_frequent_reads(fastq_r1,fastq_r2,number_of_reads_to_consider,max_paired_end_reads_overlap,min_paired_end_reads_overlap)

    curr_amplicon_id = 1

    amplicon_seq_arr = []

    #add most frequent amplicon to the list
    count,seq = seq_lines[0].strip().split()
    amplicon_seq_arr.append(seq)
    curr_amplicon_id += 1

    #for the remainder of the amplicons, test them before adding
    for i in range(1,len(seq_lines)):
        count,seq = seq_lines[i].strip().split()
        last_count,last_seq = seq_lines[i-1].strip().split()
        #if this allele is present in at least XX% of the samples
        if float(last_count)/float(number_of_reads_to_consider) > min_freq_to_consider:
            for amp_seq in amplicon_seq_arr:
                ref_incentive = np.zeros(len(amp_seq)+1,dtype=np.int)
                fws1,fws2,fwscore=cnwalign.global_align(seq,amp_seq,matrix=aln_matrix,gap_incentive=ref_incentive,gap_open=needleman_wunsch_gap_open,gap_extend=needleman_wunsch_gap_extend,)
                rvs1,rvs2,rvscore=cnwalign.global_align(reverse_complement(seq),amp_seq,matrix=aln_matrix,gap_incentive=ref_incentive,gap_open=needleman_wunsch_gap_open,gap_extend=needleman_wunsch_gap_extend,)
                #if the sequence is similar to a previously-seen read, don't add it
                if fwscore > amplicon_similarity_cutoff or rvscore > amplicon_similarity_cutoff:
                    continue
                else:
                    amplicon_seq_arr.append(seq)
                    curr_amplicon_id += 1
                    continue
        else:
            break

    return amplicon_seq_arr


######
# allele modification functions
######

def get_row_around_cut(row,cut_point,offset):
    cut_idx=row['ref_positions'].index(cut_point)
    return row['Aligned_Sequence'][cut_idx-offset+1:cut_idx+offset+1],row['Reference_Sequence'][cut_idx-offset+1:cut_idx+offset+1],row['Read_Status']=='UNMODIFIED',row['n_deleted'],row['n_inserted'],row['n_mutated'],row['#Reads'], row['%Reads']


def get_dataframe_around_cut(df_alleles, cut_point,offset):
    df_alleles_around_cut=pd.DataFrame(list(df_alleles.apply(lambda row: get_row_around_cut(row,cut_point,offset),axis=1).values),
                    columns=['Aligned_Sequence','Reference_Sequence','Unedited','n_deleted','n_inserted','n_mutated','#Reads','%Reads'])
    df_alleles_around_cut=df_alleles_around_cut.groupby(['Aligned_Sequence','Reference_Sequence','Unedited','n_deleted','n_inserted','n_mutated']).sum().reset_index().set_index('Aligned_Sequence')

    df_alleles_around_cut.sort_values(by='%Reads',inplace=True,ascending=False)
    df_alleles_around_cut['Unedited']=df_alleles_around_cut['Unedited']>0
    return df_alleles_around_cut

def get_row_around_cut_debug(row,cut_point,offset):
    cut_idx=row['ref_positions'].index(cut_point)
    #don't check overflow -- it was checked when program started
    return row['Aligned_Sequence'][cut_idx-offset+1:cut_idx+offset+1],row['Reference_Sequence'][cut_idx-offset+1:cut_idx+offset+1],row['Read_Status']=='UNMODIFIED',row['n_deleted'],row['n_inserted'],row['n_mutated'],row['#Reads'],row['%Reads'],row['Aligned_Sequence'],row['Reference_Sequence']

def get_dataframe_around_cut_debug(df_alleles, cut_point,offset):
    df_alleles_around_cut=pd.DataFrame(list(df_alleles.apply(lambda row: get_row_around_cut_debug(row,cut_point,offset),axis=1).values),
                    columns=['Aligned_Sequence','Reference_Sequence','Unedited','n_deleted','n_inserted','n_mutated','#Reads','%Reads','oSeq','oRef'])
    df_alleles_around_cut=df_alleles_around_cut.groupby(['Aligned_Sequence','Reference_Sequence','Unedited','n_deleted','n_inserted','n_mutated','oSeq','oRef']).sum().reset_index().set_index('Aligned_Sequence')

    df_alleles_around_cut.sort_values(by='%Reads',inplace=True,ascending=False)
    df_alleles_around_cut['Unedited']=df_alleles_around_cut['Unedited']>0
    return df_alleles_around_cut

def get_amplicon_info_for_guides(ref_seq,guides,quantification_window_center,quantification_window_size,quantification_window_coordinates,exclude_bp_from_left,exclude_bp_from_right,plot_window_size):
    """
    gets cut site and other info for a reference sequence and a given list of guides

    input:
    ref_seq : reference sequence
    guides : a list of guide sequences
    quantification_window_center : for each guide, quantification is centered at this position
    quantification_window_size : size of window centered at quantification_window_center
    quantification_window_coordinates: if given, these override quantification_window_center and quantification_window_size for setting quantification window
    exclude_bp_from_left : these bp are excluded from the quantification window
    exclude_bp_from_right : these bp are excluded from the quantification window
    plot_window_size : size of window centered at quantification_window_center to plot

    returns:
    this_sgRNA_sequences : list of sgRNAs that are in this amplicon
    this_sgRNA_intervals : indices of each guide
    this_cut_points : cut points for each guide (defined by quantification_window_center)
    this_sgRNA_plot_offsets : whether each guide is on the forward strand or the reverse strand -- if it's on the reverse strand, it needs to be offset by 1 in the plot
    this_include_idxs : indices to be included in quantification
    this_exclude_idxs : indices to be excluded from quantification
    this_plot_idxs : indices to be plotted
    """
    ref_seq_length = len(ref_seq)

    this_sgRNA_sequences = []
    this_sgRNA_intervals = []
    this_cut_points = []
    this_sgRNA_plot_offsets = [] #whether each guide is on the forward strand or the reverse strand -- if it's on the reverse strand, it needs to be offset by 1 in the plot
    this_include_idxs=[]
    this_exclude_idxs=[]
    this_plot_idxs=[]

    for guide_idx, current_guide_seq in enumerate(guides):
        offset_fw=quantification_window_center+len(current_guide_seq)-1
        offset_rc=(-quantification_window_center)-1
        new_cut_points=[m.start() + offset_fw for m in re.finditer(current_guide_seq, ref_seq)]+\
                         [m.start() + offset_rc for m in re.finditer(reverse_complement(current_guide_seq), ref_seq)]+\
                         [m.start() + offset_rc for m in re.finditer(reverse(current_guide_seq), ref_seq)]

        if (new_cut_points):
            this_cut_points += new_cut_points
            this_sgRNA_intervals+=[(m.start(),m.start()+len(current_guide_seq)-1) for m in re.finditer(current_guide_seq, ref_seq)]+\
                                  [(m.start(),m.start()+len(current_guide_seq)-1) for m in re.finditer(reverse_complement(current_guide_seq), ref_seq)]+\
                                  [(m.start(),m.start()+len(current_guide_seq)-1) for m in re.finditer(reverse(current_guide_seq), ref_seq)]
            this_sgRNA_sequences.append(current_guide_seq)

            if current_guide_seq in ref_seq: #if the guide is present in the forward direction
                this_sgRNA_plot_offsets.append(1)
            else:
                this_sgRNA_plot_offsets.append(0)

    #create mask of positions in which to include/exclude indels for the quantification window
    #first, if exact coordinates have been given, set those
    if quantification_window_coordinates is not None and len(quantification_window_coordinates.split(",")) > idx :
        theseCoords = quantification_window_coordinates.split(",")[idx].split("_")
        for coord in theseCoords:
            coordRE = re.match(r'^(\d+)-(\d+)$',coord)
            if coordRE:
                start = int(coordRE.group(1))
                end = int(coordRE.group(2)) + 1
                if end > ref_seq_length:
                    raise NTException("End coordinate " + str(end) + " for '" + str(coord) + "' in '" + str(theseCoords) + "' is longer than the sequence length ("+str(ref_seq_length)+")")
                this_include_idxs.extend(range(start,end))
            else:
                raise NTException("Cannot parse analysis window coordinate '" + str(coord) + "' in '" + str(theseCoords) + "'. Coordinates must be given in the form start-end e.g. 5-10 . Please check the --analysis_window_coordinate parameter.")
    elif this_cut_points and quantification_window_size>0:
        half_window=max(1,quantification_window_size/2)
        for cut_p in this_cut_points:
            st=max(0,cut_p-half_window+1)
            en=min(ref_seq_length-1,cut_p+half_window+1)
            this_include_idxs.extend(range(st,en))
    else:
       this_include_idxs=range(ref_seq_length)


    if exclude_bp_from_left:
       this_exclude_idxs+=range(exclude_bp_from_left)

    if exclude_bp_from_right:
       this_exclude_idxs+=range(ref_seq_length)[-exclude_bp_from_right:]

    #flatten the arrays to avoid errors with old numpy library
    this_include_idxs=np.ravel(this_include_idxs)
    this_exclude_idxs=np.ravel(this_exclude_idxs)

    this_include_idxs=set(np.setdiff1d(this_include_idxs,this_exclude_idxs))
    if len(this_include_idxs) == 0:
        raise BadParameterException('The entire sequence has been excluded. Please enter a longer amplicon, or decrease the exclude_bp_from_right and exclude_bp_from_left parameters')

    if this_cut_points and plot_window_size>0:
        window_around_cut=max(1,plot_window_size/2)
        for cut_p in this_cut_points:
            if cut_p - window_around_cut + 1 < 0:
                raise BadParameterException('Offset around cut would extend to the left of the amplicon. Please decrease plot_window_size parameter. Cut point: ' + str(cut_p) + ' window: ' + str(window_around_cut))
            if cut_p - window_around_cut > ref_seq_length-1:
                raise BadParameterException('Offset around cut would be greater than sequence length . Please decrease plot_window_size parameter. Cut point: ' + str(cut_p) + ' window: ' + str(window_around_cut))
            st=max(0,cut_p-window_around_cut+1)
            en=min(ref_seq_length-1,cut_p+window_around_cut+1)
            this_plot_idxs.append(range(st,en))
    else:
       this_plot_idxs=range(ref_seq_length)

    this_plot_idxs = np.ravel(this_plot_idxs)

    return this_sgRNA_sequences, this_sgRNA_intervals, this_cut_points, this_sgRNA_plot_offsets, this_include_idxs, this_exclude_idxs, this_plot_idxs


######
# terminal functions
######
def get_crispresso_logo():
    return (r'''
     _
    '  )
    .-'
   (____
C)|     \
  \     /
   \___/
''')

def get_crispresso_header(description,header_str):
    """
    Creates the CRISPResso header string with the header_str between two crispresso mugs
    """
    term_width = 80

    logo = get_crispresso_logo()
    logo_lines = logo.splitlines()
    max_logo_width = max([len(x) for x in logo_lines])

    output_line = ""
    if header_str is not None:
        header_str = header_str.strip()

        header_lines = header_str.splitlines()
        while(len(header_lines) < len(logo_lines)):
            header_lines = [""] + header_lines

        max_header_width = max([len(x) for x in header_lines])


        pad_space = (term_width - (max_logo_width*2) - max_header_width)/4 - 1
        pad_string = " " * pad_space

        for i in range(len(logo_lines))[::-1]:
            output_line = (logo_lines[i].ljust(max_logo_width) + pad_string + header_lines[i].ljust(max_header_width) + pad_string + logo_lines[i].ljust(max_logo_width)).center(term_width) + "\n" + output_line

    else:
        pad_space = (term_width - max_logo_width)/2 - 1
        pad_string = " " * pad_space
        for i in range(len(logo_lines))[::-1]:
            output_line = (pad_string + logo_lines[i].ljust(max_logo_width) + pad_string).center(term_width) + "\n" + output_line

    output_line += '\n'+('[CRISPresso version ' + __version__ + ']').center(term_width) + '\n' + ('[Kendell Clement and Luca Pinello 2018]').center(term_width) + "\n" + ('[For support, contact kclement@mgh.harvard.edu]').center(term_width) + "\n"

    description_str = ""
    for str in description:
        str = str.strip()
        description_str += str.center(term_width) + "\n"

    return "\n" + description_str + output_line

def get_crispresso_footer():
    logo = get_crispresso_logo()
    logo_lines = logo.splitlines()

    max_logo_width = max([len(x) for x in logo_lines])
    pad_space = (80 - (max_logo_width))/2 - 1
    pad_string = " " * pad_space

    output_line = ""
    for i in range(len(logo_lines))[::-1]:
        output_line = pad_string + logo_lines[i].ljust(max_logo_width) + pad_string + "\n" + output_line

    return output_line
