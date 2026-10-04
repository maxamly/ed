from ed import *
E('src/**/*.css').all['.card {'].block.all[rx(r'(\d+)px')] = \
    lambda m: f'{int(m[1]) / 16:g}rem'
