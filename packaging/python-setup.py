"""Build a platform wheel from the already staged native package."""
from pathlib import Path
from setuptools import setup, find_packages, Distribution
class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return True
packages=find_packages()
data={p:[str(f.relative_to(Path(p.replace('.', '/')))) for f in Path(p.replace('.', '/')).rglob('*')
         if f.is_file() and '__pycache__' not in f.parts and f.suffix != '.py'] for p in packages}
setup(name='terracotta-rust', version='0.1.0', description='Terracotta compatibility package with shared Rust raster/storage core',
      packages=packages, package_data=data, distclass=NativeDistribution, python_requires='>=3.10',
      entry_points={'console_scripts':['terracotta=terracotta.scripts.cli:entrypoint']},
      install_requires=['apispec>=6,<7','apispec-webframeworks','cachetools','click','click-spinner','color-operations==0.4.0',
                        'Flask>=3,<4','flask-cors','marshmallow>=3,<4','mercantile','numpy>=2,<3','Pillow','PyYAML',
                        'rasterio==1.4.3','shapely>=2,<3','SQLAlchemy>=2,<3','toml','tqdm'],
      extras_require={'operations':['colorlog','aws-xray-sdk','boto3','PyMySQL','psycopg2-binary'],
                      'colormaps':['matplotlib']})
