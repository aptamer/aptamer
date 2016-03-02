#!/usr/bin/python

"""
Create graph from a set of RNA sequences.

Vertices are RNA sequences.
Edges are created between vertices that have a small enough
tree distance or edit distance between them. (Specified in args.)

Outputs an xgmml graph file. Overall statistics are printed to
standard output.
"""

import os
import sys
import re
import subprocess
import itertools
import textwrap
import argparse
import numpy
import scipy.stats
from Bio import SeqIO
import Levenshtein


class RNASequence:
    """Graph node."""
    def __init__(self, name, cluster_size, seq):
        self.name = name  # integer ID
        self.cluster_size = int(cluster_size)
        self.sequence = seq
        self.structure = None
        self.free_energy = None
        self.ensemble_free_energy = None
        self.ensemble_probability = None
        self.ensemble_diversity = None
        self.use_for_comparison = True  # used only with seed option
        self.mfold_structure = None

    def full_output(self):
        attrs = vars(self)
        print ','.join('%s:%s' % item for item in attrs.items())

    def output(self):
        print '>%s  SIZE=%s' % (self.name, self.cluster_size)
        print self.sequence
        print self.structure

    def __str__(self):
        return '\n'.join(
            ['%s : %s' % (z, self.__dict__[z]) for z in self.__dict__]
        )


class RNASequencePair:
    """Graph edge. Pair of RNASequence objects."""
    def __init__(self, seq1, seq2, xgmml_obj):
        self.sequence1 = seq1
        self.sequence2 = seq2
        self.xgmml = xgmml_obj
        self.energy_delta = None
        self.edit_distance = None
        self.tree_distance = None
        self.is_valid_edge = False  # used only with seed option

    def __str__(self):
        return '%s\n---\n%s' % (str(self.sequence1), str(self.sequence2))

    def output(self, args):
        # if the xgmml data structure does not have this node, add it
        if self.sequence1.name not in self.xgmml.nodes:  
            self.xgmml.nodes[self.sequence1.name] = self.sequence1
        if self.sequence2.name not in self.xgmml.nodes:
            self.xgmml.nodes[self.sequence2.name] = self.sequence2

        # make edge between nodes that are similar enough
        # in terms of edit or tree distance
        if args.edge_type in ['edit', 'both']:
            if (
                self.edit_distance and
                (int(self.edit_distance) <= args.max_edit_dist)
            ):
                self.xgmml.edges.append([
                    self.sequence1.name, self.sequence2.name,
                    self.edit_distance, 'edit_distance'
                ])
                self.is_valid_edge = True
        if args.edge_type in ['tree', 'both']:
            if (
                self.tree_distance and
                (int(self.tree_distance) <= args.max_tree_dist)
            ):
                self.xgmml.edges.append([
                    self.sequence1.name, self.sequence2.name,
                    self.tree_distance, 'tree_distance'
                ])
                self.is_valid_edge = True


class XGMML:
    """Graph. XGMML file."""
    def __init__(self, name):
        self.name = name
        self.out_str = ''
        self.nodes = {}

        # list of tuples
        # (source ID, target ID, attribute value, attribute name)
        self.edges = []

    def output_att(self, type_, name, label, value):
        self.out_str += (
            '<att type="%s" name="%s" label="%s" value="%s"/>\n' % (
                type_, name, label, value
            )
        )

    def output(self, args):
        self.out_str = textwrap.dedent(
            """
            <?xml version="1.0"?>
            <graph directed="1" id="5" label="%s"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:ns1="http://www.w3.org/1999/xlink"
            xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
            xmlns="http://www.cs.rpi.edu/XGMML">
            """ % self.name
        ).lstrip()
        for n in self.nodes:
            self.out_str += '<node id="%s" label="%s" weight="%s">\n' % (
                n, self.nodes[n].name, self.nodes[n].cluster_size
            )
            self.output_att(
                'integer', 'size', 'Size', self.nodes[n].cluster_size
            )
            self.output_att(
                'string', 'structure', 'Structure', self.nodes[n].structure
            )
            if args.run_mfold:
                self.output_att(
                    'string', 'mfoldStructure', 'mfold Structure',
                    self.nodes[n].mfold_structure
                )
            self.output_att(
                'string', 'sequence', 'Sequence', self.nodes[n].sequence
            )
            self.output_att(
                'real', 'energy', 'Energy', self.nodes[n].free_energy
            )
            self.output_att(
                'real', 'ensembleFreeEnergy', 'ensemble Free Energy',
                self.nodes[n].ensemble_free_energy
            )
            self.output_att(
                'real', 'ensembleProbability', 'ensemble Probability',
                self.nodes[n].ensemble_probability
            )
            self.output_att(
                'real', 'ensembleDiversity', 'ensemble Diversity',
                self.nodes[n].ensemble_diversity
            )
            self.out_str += '</node>\n'

        for e in self.edges:
            self.out_str += (
                '<edge source="%s" target="%s" label="%s to %s" >\n' % (
                    e[0], e[1], e[0], e[1]
                )
            )
            self.output_att('string', e[3], 'interaction', e[2])
            self.out_str += '</edge>\n'
        self.out_str += '</graph>\n'
        return self.out_str


####### MAIN #######
def main():
    args = parse_arguments()

    cluster_size_re = re.compile('SIZE=(\d+)')
    in_fname = args.input_file
    in_fh = open(in_fname)

    # stats to be used if the input is in fasta format
    # stats are given default values if input is non-fasta
    stats = {'energy_delta':[], 'edit_distance':[], 'tree_distance':[]}

    rna_seq_objs = []  # list of RNASequence objects (graph vertices)
    if not args.no_fasta:
        process_fasta(in_fh, args, cluster_size_re, rna_seq_objs)
    else:
        process_non_fasta(in_fh, args, cluster_size_re, rna_seq_objs)

    xgmml_obj = XGMML(in_fname)

    # node data structures are now populated. find edges.
    print 'Finding edges...'
    if args.seed:
        find_edges_seed(rna_seq_objs, xgmml_obj, args, stats)
    else:
        find_edges_no_seed(rna_seq_objs, xgmml_obj, args, stats)

    # output xgmml file
    out_xgmml_fname = in_fname + '.xgmml'
    if not args.no_xgmml:
        cystoscape_out = open(out_xgmml_fname, 'w')
        cystoscape_out.write(xgmml_obj.output(args))
        cystoscape_out.close()

    print_stats(stats, args)

    if not args.no_xgmml:
        print '\n\nOutput written to %s.' % out_xgmml_fname

    in_fh.close()


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            '%s: Create xgmml graph and calculate statistics '
            'from a set of RNA sequences.' % (
                os.path.basename(sys.argv[0])
            )
        )
    )
    parser.add_argument(
        'input_file', help=(
            'Input file containing RNA sequences. (Preferably in '
            'fasta format.)'
        )
    )
    parser.add_argument(
        '-t', '--edge_type', default='both', help=(
            'Whether to create edges in output graph according to '
            'edit distance or tree distance.'
            '(Specify "edit", "tree", or "both".) (Default: both)'
        )
    )
    parser.add_argument(
        '-e', '--max_edit_dist', type=int, default=3,
        help=(
            'Maximum edit distance allowed for an edge to be created '
            'in output graph. '
            'Assumes --edge_type is "edit" or "both". (Default: 3)'
        )
    )
    parser.add_argument(
        '-d', '--max_tree_dist', type=int, default=3,
        help=(
            'Maximum tree distance allowed for an edge to be created '
            'in output graph. '
            'Assumes --edge_type is "tree" or "both". (Default: 3)'
        )
    )
    parser.add_argument(
        '-v', '--vienna_version', type=int, default=2,
        help=(
            'ViennaRNA package version. Specify "1" or "2". '
            '(Default: 2)'
        )
    )
    parser.add_argument(
        '-m', '--run_mfold', action='store_true', default=False,
        help=(
            'Run mfold (in addition to Vienna RNAFold) to predict '
            'RNA structure.'
        )
    )
    parser.add_argument(
        '-p', '--prefix', default='GGGAGGACGAUGCGG', help=(
            'Sequence to prepend to RNA sequences during '
            'structure prediction. '
            '(Default: GGGAGGACGAUGCG)'
        )
    )
    parser.add_argument(
        '-s', '--suffix', default='CAGACGACUCGCCCGA', help= (
            'Sequence to append to RNA sequences during '
            'structure prediction. '
            '(Default: CAGACGACUCGCCCGA)'
        )
    )
    parser.add_argument(
        '--seed', action='store_true', default=False,
        help='Use seed sequence algorithm to find edges.'
    )
    parser.add_argument(
        '--no_fasta', action='store_true', default=False,
        help=(
            'Do not process input as fasta. '
            '(Some statistics will not be calculated.)'
        )
    )
    parser.add_argument(
        '--no_xgmml', action='store_true', default=False,
        help='Do not produce XGMML output file.'
    )
    parser.usage = parser.print_help()

    args = parser.parse_args()

    # validate args
    args.edge_type = args.edge_type.lower()
    if args.edge_type not in ['edit', 'tree', 'both']:
        parser.print_help()
        print 'Error: Edge type option not recognized: %s' % (
            args.edge_type
        )
        sys.exit(1)

    if args.vienna_version not in ['1', '2', 1, 2]:
        parser.print_help()
        print 'Error: ViennaRNA package version not recognized: %s' % (
            args.vienna_version
        )
        sys.exit(1)

    return args


def run_rnafold(seq, vienna_version):
    print '##################'
    print 'Running RNAFold...'
    print '##################'
    cmd = None
    if vienna_version == 1:
        cmd = ['RNAfold -p -T 30 -noLP -noPS -noGU']
    elif vienna_version == 2:
        cmd = ['RNAfold -p -T 30 --noLP --noPS --noGU']
    sffproc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE, close_fds=True, shell=True
    )
    stdout_value, stderr_value = sffproc.communicate(seq)
    return stdout_value


def run_mfold(seq):
    print '##################'
    print 'Running mfold...'
    print '##################'
    if not os.path.exists('mfold_out'):
        os.mkdir('mfold_out')
    os.chdir('mfold_out')
    temp_filename = 'mfold_temp.txt'
    with open(temp_filename, 'w') as f:
        f.write('%s\n' % seq)
    ret = subprocess.call(
        ('mfold SEQ=%s T=30' % temp_filename),
        stderr=subprocess.STDOUT, shell=True
    )
    if ret != 0:
        print (
            'Error when running mfold. Return code %d. '
            'See mfold log file for details.' % ret
        )
        sys.exit(ret)
    print
    mfold_structure = convert_ct_to_bracket_dot('%s.ct' % temp_filename)
    os.chdir('..')
    return mfold_structure


def rna_distance(structures):
    cmd = ['RNAdistance']
    sffproc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE, close_fds=True, shell=True
    )
    stdout_value, stderr_value = sffproc.communicate(structures)
    return stdout_value


def process_seq_pairs(seq_pairs, args, stats):
    """Runs a batch of RNASequence pairs."""
    #print '\n\n'.join([str(z) for z in seq_pairs])
    seqToTree = []
    for x in seq_pairs:
        seqToTree.append(x.sequence1.structure)
        seqToTree.append(x.sequence2.structure)
    tree_distance = rna_distance('\n'.join(seqToTree))
    tree_distance = tree_distance.strip('\n').split('\n')  # take off last lr
    assert len(tree_distance) == len(seq_pairs)
    for i, x in enumerate(seq_pairs):
        seq_pairs[i].tree_distance = tree_distance[i].split(' ')[1]
        x.output(args)
        stats['energy_delta'].append(x.energy_delta)
        stats['edit_distance'].append(x.edit_distance)
        try:
            stats['tree_distance'].append(float(x.tree_distance))
        except ValueError:
            stats['tree_distance'].append(None)


def convert_ct_to_bracket_dot(ct_filename):
    bracket_dot = ''
    with open(ct_filename) as f:
        for row in f:
            row = row.split()
            if '=' in row and len(row) < 6:  # first row
                energy = row[3]
            elif row[4] == '0':
                bracket_dot += '.'
            elif int(row[0]) < int(row[4]):
                bracket_dot += '('
            elif int(row[0]) > int(row[4]):
                bracket_dot += ')'
    return '%s (%s)' % (bracket_dot, energy) if (bracket_dot != '') else None


def process_fasta(in_fh, args, cluster_size_re, rna_seq_objs):
    """Process input file as fasta. Populate RNASequence (graph vertex)
    objects.
    """
    for record in SeqIO.parse(in_fh, 'fasta'):
        sequence = '%s%s%s'.replace('T', 'U') % (
            args.prefix, str(record.seq), args.suffix
        )
        cluster_size = 1
        try:
            cluster_size = cluster_size_re.search(record.description)
            cluster_size = cluster_size.group(1)
        except AttributeError:
            print 'Not able to find cluster size. Setting to 1.'
        if cluster_size is None:
            cluster_size = 1

        curr_seq = RNASequence(record.id, cluster_size, sequence)
        rnafold_out = run_rnafold(sequence, args.vienna_version)
        rnafold_out = rnafold_out.split('\n')
        print '%s\n' % rnafold_out
        curr_seq.structure, curr_seq.free_energy = (
            rnafold_out[1].split(' (')
        )
        curr_seq.free_energy = abs(
            float(curr_seq.free_energy.replace(')', ''))
        )
        curr_seq.ensemble_free_energy = abs(
            float(rnafold_out[2].split('[')[1].replace(']', ''))
        )
        curr_seq.ensemble_probability = abs(float(
            rnafold_out[4].split(';')[0].replace(
                ' frequency of mfe structure in ensemble ', ''
            )
        ))
        curr_seq.ensemble_diversity = abs(float(
            rnafold_out[4].split(';')[1].replace(
                ' ensemble diversity ', ''
            )
        ))
        rna_seq_objs.append(curr_seq)
        if args.run_mfold:
            mfold_structure = run_mfold(sequence)
            curr_seq.mfold_structure = mfold_structure
        else:
            curr_seq.mfold_structure = None


def process_non_fasta(in_fh, args, cluster_size_re, rna_seq_objs):
    """Process non-fasta input file. Populate RNASequence
    (graph vertex) objects.
    """
    while True:
        try:
            # need to move through a triplet file structure, not fasta
            header, sequence, structure = list(
                itertools.islice(in_fh, 3)
            )
        except ValueError:  # end of file
            break
        sequence = sequence.strip('\n\r')
        structure = structure.strip('\n\r')
        if not structure.count('(') == structure.count(')'):
            continue
        sequence = '%s%s%s'.replace('T', 'U') % (
            args.prefix, sequence, args.suffix
        )
        try:
            cluster_size = cluster_size_re.search(header)
            cluster_size = cluster_size.group(1)
        except AttributeError:
            print 'Not able to find cluster size. Setting to 1.'
        if cluster_size is None:
            cluster_size = 1
        header = header.replace('>', '')
        header = header.split('SIZE=')[0]
        curr_seq = RNASequence(header, cluster_size, sequence)
        curr_seq.free_energy = 1
        curr_seq.ensemble_free_energy = 1
        curr_seq.ensemble_probability = 1
        curr_seq.ensemble_diversity = 1
        curr_seq.structure = structure
        rna_seq_objs.append(curr_seq)


def find_edges_seed(rna_seq_objs, xgmml_obj, args, stats):
    """Find graph edges using seed algorithm."""
    while len(rna_seq_objs) > 2:
        rna_seq_objs[0].use_for_comparison = False
        seq_pairs = []
         # go through and find all the matches
         # then remove matches and start again till gone
        for x in range(1, len(rna_seq_objs) - 1):
            # new rna_seq_objs[0] each time a node is deleted
            pair = RNASequencePair(
                rna_seq_objs[0], rna_seq_objs[x], xgmml_obj
            )
            pair.energy_delta = abs(
                pair.sequence1.free_energy - pair.sequence2.free_energy
            )
            pair.edit_distance = Levenshtein.distance(
                pair.sequence1.sequence, pair.sequence2.sequence
            )
            seq_pairs.append(pair)
        process_seq_pairs(seq_pairs, args, stats)
        for x in seq_pairs:
            if x.is_valid_edge:
                x.sequence2.use_for_comparison = False
        # delete nodes which already belong to an edge
        new_rna_seq_objs = [
            z for z in rna_seq_objs if z.use_for_comparison
        ]
        print 'Number of RNA sequences reduced from %d to %d ' % (
            len(rna_seq_objs), len(new_rna_seq_objs)
        )
        rna_seq_objs = new_rna_seq_objs


def find_edges_no_seed(rna_seq_objs, xgmml_obj, args, stats):
    """"Find edges using non-seed algorithm."""
    seq_pairs = []
     # this makes the edges. looking at each pair of nodes
    for i in range(0, len(rna_seq_objs)):
        for j in range(i + 1, len(rna_seq_objs)):
            pair = RNASequencePair(
                rna_seq_objs[i], rna_seq_objs[j], xgmml_obj
            )
            pair.energy_delta = abs(
                pair.sequence1.free_energy - pair.sequence2.free_energy
            )
            pair.edit_distance = Levenshtein.distance(
                pair.sequence1.sequence, pair.sequence2.sequence
            )
            seq_pairs.append(pair)
            # group things in batches of 10000  to find tree distances
            if len(seq_pairs) > 10000:
                process_seq_pairs(seq_pairs, args, stats)
                # zero out the seq_pairs array and start refilling again
                seq_pairs = []
    # flush out the last of the tree distance seq_pairs
    process_seq_pairs(seq_pairs, args, stats)


def print_stats(stats, args):
    print '\nOverall Statistics:'
    print '--------------------'
    for stat in stats:
        stat_label = stat.capitalize().replace('_', ' ')
        if any(stats[stat]):
            print '%s mean: %.3g' % (stat_label, numpy.mean(stats[stat]))
            print '%s SD: %.3g' % (stat_label, numpy.std(stats[stat]))
            print '%s SEM: %.3g' % (stat_label, scipy.stats.sem(stats[stat]))
        else:
            print '%s mean: N/A' % (stat_label)
            print '%s SD: N/A' % (stat_label)
            print '%s SEM: N/A' % (stat_label)
        print

    for stat_pair in itertools.combinations(stats, 2):
        print 'Pearson correlation(%s, %s):' % (
            stat_pair[0], stat_pair[1]
        ),
        if not args.no_fasta:
            pearson_coeff, p_value = scipy.stats.pearsonr(
                stats[stat_pair[0]], stats[stat_pair[1]]
            )
            print '%.3g, p=%.3g' % (pearson_coeff, p_value)
        else:
            print 'N/A'


if __name__ == '__main__':
    sys.exit(main())
